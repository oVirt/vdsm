#
# Copyright 2008-2017 Red Hat, Inc.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#
from __future__ import absolute_import

from operator import itemgetter

from vdsm.common import conv
from vdsm.virt import metadata
from vdsm.virt import vmxml
from vdsm.virt import xmlconstants
from vdsm import constants
from vdsm import cpuarch


_BOOT_MENU_TIMEOUT = 10000  # milliseconds

_DEFAULT_MACHINES = {
    cpuarch.X86_64: 'pc',
    cpuarch.PPC64: 'pseries',
    cpuarch.PPC64LE: 'pseries',
}


class Domain(object):

    def __init__(self, conf, log, arch):
        """
        Create the skeleton of a libvirt domain xml

        <domain type="kvm">
            <name>vmName</name>
            <uuid>9ffe28b6-6134-4b1e-8804-1185f49c436f</uuid>
            <memory>262144</memory>
            <currentMemory>262144</currentMemory>
            <vcpu current='smp'>160</vcpu>
            <devices>
            </devices>
        </domain>

        """
        self.conf = conf
        self.log = log

        self.arch = arch

        if conv.tobool(self.conf.get('kvmEnable', 'true')):
            domainType = 'kvm'
        else:
            domainType = 'qemu'

        domainAttrs = {'type': domainType}

        self.dom = vmxml.Element('domain', **domainAttrs)

        self.dom.appendChildWithArgs('name', text=self.conf['vmName'])
        self.dom.appendChildWithArgs('uuid', text=self.conf['vmId'])
        if 'numOfIoThreads' in self.conf:
            self.dom.appendChildWithArgs('iothreads',
                                         text=str(self.conf['numOfIoThreads']))
        memSizeKB = str(int(self.conf.get('memSize', '256')) * 1024)
        self.dom.appendChildWithArgs('memory', text=memSizeKB)
        self.dom.appendChildWithArgs('currentMemory', text=memSizeKB)
        if 'maxMemSize' in self.conf:
            maxMemSizeKB = str(int(self.conf['maxMemSize']) * 1024)
            maxMemSlots = str(self.conf.get('maxMemSlots', '16'))
            self.dom.appendChildWithArgs('maxMemory', text=maxMemSizeKB,
                                         slots=maxMemSlots)
        vcpu = self.dom.appendChildWithArgs('vcpu', text=self._getMaxVCpus())
        vcpu.setAttrs(**{'current': self._getSmp()})

        self._devices = vmxml.Element('devices')
        self.dom.appendChild(self._devices)

    def appendClock(self):
        """
        Add <clock> element to domain:

        <clock offset="variable" adjustment="-3600">
            <timer name="rtc" tickpolicy="catchup">
        </clock>

        for hyperv:
        <clock offset="variable" adjustment="-3600">
            <timer name="hypervclock" present="yes">
            <timer name="rtc" tickpolicy="catchup">
        </clock>
        """

        m = vmxml.Element('clock', offset='variable',
                          adjustment=str(self.conf.get('timeOffset', 0)))
        if conv.tobool(self.conf.get('hypervEnable', 'false')):
            m.appendChildWithArgs('timer', name='hypervclock', present='yes')
        m.appendChildWithArgs('timer', name='rtc', tickpolicy='catchup')
        m.appendChildWithArgs('timer', name='pit', tickpolicy='delay')

        if cpuarch.is_x86(self.arch):
            m.appendChildWithArgs('timer', name='hpet', present='no')

        self.dom.appendChild(m)

    def appendMetadata(self):
        """
        Add the namespaced qos metadata element to the domain

        <domain xmlns:ovirt="http://ovirt.org/vm/tune/1.0">
        ...
           <metadata>
              <ovirt:qos xmlns:ovirt=>
           </metadata>
        ...
        </domain>
        """

        metadata_elem = vmxml.Element('metadata')
        vmxml.append_child(
            metadata_elem,
            etree_child=metadata.create(
                xmlconstants.METADATA_VM_TUNE_ELEMENT,
                namespace=xmlconstants.METADATA_VM_TUNE_PREFIX,
                namespace_uri=xmlconstants.METADATA_VM_TUNE_URI
            )
        )
        vmxml.append_child(
            metadata_elem,
            etree_child=metadata.create(
                xmlconstants.METADATA_VM_VDSM_ELEMENT,
                namespace=xmlconstants.METADATA_VM_VDSM_PREFIX,
                namespace_uri=xmlconstants.METADATA_VM_VDSM_URI
            )
        )

        self._appendMetadataContainer(metadata_elem)
        self.dom.appendChild(metadata_elem)

    def _appendMetadataContainer(self, metadata_elem):
        custom = self.conf.get('custom', {})
        # container{Type,Image} are mandatory: if either
        # one is missing, no container-related extradata
        # should be present at all.
        container_type = custom.get('containerType')
        container_image = custom.get('containerImage')
        if not container_type or not container_image:
            return

        vmxml.append_child(
            metadata_elem,
            etree_child=metadata.create(
                xmlconstants.METADATA_CONTAINERS_ELEMENT,
                namespace=xmlconstants.METADATA_CONTAINERS_PREFIX,
                namespace_uri=xmlconstants.METADATA_CONTAINERS_URI,
                runtime=container_type,
                image=container_image
            )
        )

        # drive mapping is optional. It is totally fine for a container
        # not to use any drive, this just means it will not have any
        # form of persistency.
        drive_map = parse_drive_mapping(self.conf.get('custom', {}))
        if drive_map:
            vmxml.append_child(
                metadata_elem,
                etree_child=metadata.create(
                    xmlconstants.METADATA_VM_DRIVE_MAP_ELEMENT,
                    namespace=xmlconstants.METADATA_VM_DRIVE_MAP_PREFIX,
                    namespace_uri=xmlconstants.METADATA_VM_DRIVE_MAP_URI,
                    **drive_map
                )
            )

    def appendOs(self, use_serial_console=False):
        """
        Add <os> element to domain:

        <os>
            <type arch="x86_64" machine="pc">hvm</type>
            <boot dev="cdrom"/>
            <kernel>/tmp/vmlinuz-2.6.18</kernel>
            <initrd>/tmp/initrd-2.6.18.img</initrd>
            <cmdline>ARGs 1</cmdline>
            <smbios mode="sysinfo"/>
        </os>

        If 'use_serial_console' is true and we are on x86, use the console:

        <os>
            ...
            <bios useserial="yes"/>
        </os>

        """

        oselem = vmxml.Element('os')
        self.dom.appendChild(oselem)

        machine = self.conf.get('emulatedMachine',
                                _DEFAULT_MACHINES[self.arch])

        oselem.appendChildWithArgs('type', text='hvm', arch=self.arch,
                                   machine=machine)

        qemu2libvirtBoot = {'a': 'fd', 'c': 'hd', 'd': 'cdrom', 'n': 'network'}
        for c in self.conf.get('boot', ''):
            oselem.appendChildWithArgs('boot', dev=qemu2libvirtBoot[c])

        if self.conf.get('initrd'):
            oselem.appendChildWithArgs('initrd', text=self.conf['initrd'])

        if self.conf.get('kernel'):
            oselem.appendChildWithArgs('kernel', text=self.conf['kernel'])

        if self.conf.get('kernelArgs'):
            oselem.appendChildWithArgs('cmdline', text=self.conf['kernelArgs'])

        if cpuarch.is_x86(self.arch):
            oselem.appendChildWithArgs('smbios', mode='sysinfo')

        if conv.tobool(self.conf.get('bootMenuEnable', False)):
            oselem.appendChildWithArgs('bootmenu', enable='yes',
                                       timeout=str(_BOOT_MENU_TIMEOUT))

        if use_serial_console and cpuarch.is_x86(self.arch):
            oselem.appendChildWithArgs('bios', useserial='yes')

    def appendSysinfo(self, osname, osversion, serialNumber):
        """
        Add <sysinfo> element to domain:

        <sysinfo type="smbios">
          <system>
            <entry name="manufacturer">Fedora</entry>
            <entry name="product">Virt-Manager</entry>
            <entry name="version">0.8.2-3.fc14</entry>
            <entry name="serial">32dfcb37-5af1-552b-357c-be8c3aa38310</entry>
            <entry name="uuid">c7a5fdbd-edaf-9455-926a-d65c16db1809</entry>
          </system>
        </sysinfo>
        """

        sysinfoelem = vmxml.Element('sysinfo', type='smbios')
        self.dom.appendChild(sysinfoelem)

        syselem = vmxml.Element('system')
        sysinfoelem.appendChild(syselem)

        def appendEntry(k, v):
            syselem.appendChildWithArgs('entry', text=v, name=k)

        appendEntry('manufacturer', constants.SMBIOS_MANUFACTURER)
        appendEntry('product', osname)
        appendEntry('version', osversion)
        appendEntry('serial', serialNumber)
        appendEntry('uuid', self.conf['vmId'])

    def appendFeatures(self):
        """
        Add machine features to domain xml.

        Currently only
        <features>
            <acpi/>
        <features/>

        for hyperv:
        <features>
            <acpi/>
            <hyperv>
                <relaxed state='on'/>
            </hyperv>
        <features/>
        """

        if (conv.tobool(self.conf.get('acpiEnable', 'true')) or
                conv.tobool(self.conf.get('hypervEnable', 'false'))):
            features = self.dom.appendChildWithArgs('features')

        if conv.tobool(self.conf.get('acpiEnable', 'true')):
            features.appendChildWithArgs('acpi')

        if conv.tobool(self.conf.get('hypervEnable', 'false')):
            hyperv = vmxml.Element('hyperv')
            features.appendChild(hyperv)

            hyperv.appendChildWithArgs('relaxed', state='on')
            # turns off an internal Windows watchdog, and by doing so avoids
            # some high load BSODs.
            hyperv.appendChildWithArgs('vapic', state='on')
            # magic number taken from recomendations. References:
            # https://bugzilla.redhat.com/show_bug.cgi?id=1083529#c10
            # https://bugzilla.redhat.com/show_bug.cgi?id=1053846#c0
            hyperv.appendChildWithArgs(
                'spinlocks', state='on', retries='8191')

    def appendCpu(self, hugepages_shared=False):
        """
        Add guest CPU definition.

        <cpu match="exact">
            <model>qemu64</model>
            <topology sockets="S" cores="C" threads="T"/>
            <feature policy="require" name="sse2"/>
            <feature policy="disable" name="svm"/>
        </cpu>

        For POWER8, there is no point in trying to use baseline CPU for flags
        since there are only HW features. There are 2 ways of creating a valid
        POWER8 element that we support:

            <cpu>
                <model>POWER{X}</model>
            </cpu>

        This translates to -cpu POWER{X} (where {X} is version of the
        processor - 7 and 8), which tells qemu to emulate the CPU in POWER8
        family that it's capable of emulating - in case of hardware
        virtualization, that will be the host cpu (so an equivalent of
        -cpu host). Using this option does not limit migration between POWER8
        machines - it is still possible to migrate from e.g. POWER8 to
        POWER8e. The second option is not supported and serves only for
        reference:

            <cpu mode="host-model">
                <model>power{X}</model>
            </cpu>

        where {X} is the binary compatibility version of POWER that we
        require (6, 7, 8). This translates to qemu's -cpu host,compat=power{X}.

        Using the second option also does not limit migration between POWER8
        machines - it is still possible to migrate from e.g. POWER8 to POWER8e.
        """

        cpu = vmxml.Element('cpu')

        if cpuarch.is_x86(self.arch):
            cpu.setAttrs(match='exact')

            features = self.conf.get('cpuType', 'qemu64').split(',')
            model = features[0]

            if model == 'hostPassthrough':
                cpu.setAttrs(mode='host-passthrough')
            elif model == 'hostModel':
                cpu.setAttrs(mode='host-model')
            else:
                cpu.appendChildWithArgs('model', text=model)

                # This hack is for backward compatibility as the libvirt
                # does not allow 'qemu64' guest on intel hardware
                if model == 'qemu64' and '+svm' not in features:
                    features += ['-svm']

                for feature in features[1:]:
                    # convert Linux name of feature to libvirt
                    if feature[1:6] == 'sse4_':
                        feature = feature[0] + 'sse4.' + feature[6:]

                    featureAttrs = {'name': feature[1:]}
                    if feature[0] == '+':
                        featureAttrs['policy'] = 'require'
                    elif feature[0] == '-':
                        featureAttrs['policy'] = 'disable'
                    cpu.appendChildWithArgs('feature', **featureAttrs)
        elif cpuarch.is_ppc(self.arch):
            features = self.conf.get('cpuType', 'POWER8').split(',')
            model = features[0]
            cpu.appendChildWithArgs('model', text=model)

        if ('smpCoresPerSocket' in self.conf or
                'smpThreadsPerCore' in self.conf):
            maxVCpus = int(self._getMaxVCpus())
            cores = int(self.conf.get('smpCoresPerSocket', '1'))
            threads = int(self.conf.get('smpThreadsPerCore', '1'))
            cpu.appendChildWithArgs('topology',
                                    sockets=str(maxVCpus / cores / threads),
                                    cores=str(cores), threads=str(threads))

        # CPU-pinning support
        # see http://www.ovirt.org/wiki/Features/Design/cpu-pinning
        if 'cpuPinning' in self.conf:
            cputune = vmxml.Element('cputune')
            cpuPinning = self.conf.get('cpuPinning')
            for cpuPin in cpuPinning.keys():
                cputune.appendChildWithArgs('vcpupin', vcpu=cpuPin,
                                            cpuset=cpuPinning[cpuPin])
            self.dom.appendChild(cputune)

        # Guest numa topology support
        # see http://www.ovirt.org/Features/NUMA_and_Virtual_NUMA
        if 'guestNumaNodes' in self.conf:
            numa = vmxml.Element('numa')
            guestNumaNodes = sorted(
                self.conf.get('guestNumaNodes'), key=itemgetter('nodeIndex'))
            for vmCell in guestNumaNodes:
                nodeMem = int(vmCell['memory']) * 1024
                numa_args = {'cpus': vmCell['cpus'], 'memory': str(nodeMem)}
                if hugepages_shared:
                    numa_args.update({'memAccess': 'shared'})
                numa.appendChildWithArgs('cell', **numa_args)
            cpu.appendChild(numa)

        self.dom.appendChild(cpu)

    # Guest numatune support
    def appendNumaTune(self):
        """
        Add guest numatune definition.

        <numatune>
            <memory mode='strict' nodeset='0-1'/>
            <memnode cellid='0' mode='strict' nodeset='1'>
        </numatune>
        """

        numaTune = self.conf.get('numaTune')

        numatune = vmxml.Element('numatune')
        mode = numaTune.get('mode', 'strict')

        numaTuneExists = False

        if 'nodeset' in numaTune:
            numaTuneExists = True
            numatune.appendChildWithArgs('memory',
                                         mode=mode,
                                         nodeset=numaTune['nodeset'])

        for memnode in numaTune.get('memnodes', []):
            numaTuneExists = True
            numatune.appendChildWithArgs('memnode',
                                         mode=mode,
                                         cellid=memnode['vmNodeIndex'],
                                         nodeset=memnode['nodeset'])

        if numaTuneExists:
            self.dom.appendChild(numatune)

    def appendHostdevNumaTune(self, devices):
        """
        Automatically generate numatune for VM with host devices. This tuning
        should prefer numa nodes where device's MMIO region resides.
        """

        numatune = vmxml.Element('numatune')
        numa_map = [dev_object.numa_node for dev_object in devices if
                    dev_object.is_hostdevice and dev_object.numa_node]
        if len(set(numa_map)) == 1:
            numatune.appendChildWithArgs('memory', mode='preferred',
                                         nodeset=numa_map[0])
            self.dom.appendChild(numatune)

    def _appendAgentDevice(self, path, name):
        """
          <channel type='unix'>
             <target type='virtio' name='org.linux-kvm.port.0'/>
             <source mode='bind' path='/tmp/socket'/>
          </channel>
        """
        channel = vmxml.Element('channel', type='unix')
        channel.appendChildWithArgs('target', type='virtio', name=name)
        channel.appendChildWithArgs('source', mode='bind', path=path)
        self._devices.appendChild(channel)

    def appendInput(self):
        """
        Add input device.

        <input bus="ps2" type="mouse"/>
        """
        if conv.tobool(self.conf.get('tabletEnable')):
            inputAttrs = {'type': 'tablet', 'bus': 'usb'}
        elif cpuarch.is_x86(self.arch):
            inputAttrs = {'type': 'mouse', 'bus': 'ps2'}
        else:
            inputAttrs = {'type': 'mouse', 'bus': 'usb'}

        self._devices.appendChildWithArgs('input', **inputAttrs)

    def appendEmulator(self):
        emulatorPath = '/usr/bin/qemu-system-' + self.arch

        emulator = vmxml.Element('emulator', text=emulatorPath)

        self._devices.appendChild(emulator)

    def appendDeviceXML(self, deviceXML):
        self._devices.appendChild(etree_element=vmxml.parse_xml(deviceXML))

    def appendMemoryBacking(self, hugepagesz):
        memorybacking = vmxml.Element('memoryBacking',)
        hugepages_element = vmxml.Element('hugepages')

        hugepages_element.appendChildWithArgs(
            'page', size=str(hugepagesz)
        )
        memorybacking.appendChild(hugepages_element)
        self.dom.appendChild(memorybacking)

    def toxml(self):
        return vmxml.format_xml(self.dom, pretty=True)

    def _getSmp(self):
        return self.conf.get('smp', '1')

    def _getMaxVCpus(self):
        return self.conf.get('maxVCpus', self._getSmp())


def make_minimal_domain(dom):
    """
    Enhance a Domain object, appending all the elements which
    - are not devices - which require extra logic
    - don't need additional logic or parameters, besides the trivial
      check on the CPU architecture.

    Args:
        dom (libvirtxml.Domain): domain object to enhance. It is recommended
            to use a freshly-built domain object, whose append* methods are
            not yet being called.

    Example:

    dom = make_minimal_domain(Domain(conf, log, arch))
    """
    dom.appendMetadata()
    dom.appendClock()
    if cpuarch.is_x86(dom.arch):
        dom.appendFeatures()
    return dom


def make_placeholder_domain_xml(vm):
    return '''<domain type='kvm'>
  <name>{name}</name>
  <uuid>{id}</uuid>
  <memory unit='KiB'>{memory}</memory>
  <os>
    <type arch="{arch}" machine="{machine}">hvm</type>
  </os>
</domain>'''.format(name=vm.name, id=vm.id, memory=vm.mem_size_mb(),
                    arch=vm.arch, machine=_DEFAULT_MACHINES[vm.arch])


def parse_drive_mapping(custom):
    mappings = custom.get('volumeMap', None)
    if mappings is None:
        return {}

    drive_mapping = {}
    for mapping in mappings.split(','):
        name, drive = mapping.strip().split(':', 1)
        drive_mapping[name.strip()] = drive.strip()
    return drive_mapping
