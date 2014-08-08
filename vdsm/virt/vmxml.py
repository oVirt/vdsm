#
# Copyright 2008-2014 Red Hat, Inc.
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

import sys
from operator import itemgetter
import xml.dom
import xml.dom.minidom

from vdsm import constants
from vdsm import utils

import caps


def has_channel(domXML, name):
    domObj = xml.dom.minidom.parseString(domXML)
    devices = domObj.getElementsByTagName('devices')

    if len(devices) == 1:
        for chan in devices[0].getElementsByTagName('channel'):
            targets = chan.getElementsByTagName('target')
            if len(targets) == 1:
                if targets[0].getAttribute('name') == name:
                    return True

    return False


def all_devices(domXML):
    domObj = xml.dom.minidom.parseString(domXML)
    devices = domObj.childNodes[0].getElementsByTagName('devices')[0]

    for deviceXML in devices.childNodes:
        if deviceXML.nodeType == xml.dom.Node.ELEMENT_NODE:
            yield deviceXML


def filter_devices_with_alias(devices):
    for deviceXML in devices:
        aliasElement = deviceXML.getElementsByTagName('alias')
        if aliasElement:
            alias = aliasElement[0].getAttribute('name')
            yield deviceXML, alias


class Element(object):

    def __init__(self, tagName, text=None, **attrs):
        self._elem = xml.dom.minidom.Document().createElement(tagName)
        self.setAttrs(**attrs)
        if text is not None:
            self.appendTextNode(text)

    def __getattr__(self, name):
        return getattr(self._elem, name)

    def setAttrs(self, **attrs):
        for attrName, attrValue in attrs.iteritems():
            self._elem.setAttribute(attrName, attrValue)

    def appendTextNode(self, text):
        textNode = xml.dom.minidom.Document().createTextNode(text)
        self._elem.appendChild(textNode)

    def appendChild(self, element):
        self._elem.appendChild(element)

    def appendChildWithArgs(self, childName, text=None, **attrs):
        child = Element(childName, text, **attrs)
        self._elem.appendChild(child)
        return child


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
            <memtune>
                <min_guarantee>0</min_guarantee>
            </memtune>
        </domain>

        """
        self.conf = conf
        self.log = log

        self.arch = arch

        self.doc = xml.dom.minidom.Document()

        if utils.tobool(self.conf.get('kvmEnable', 'true')):
            domainType = 'kvm'
        else:
            domainType = 'qemu'

        domainAttrs = {'type': domainType}

        # Hack around libvirt issue BZ#988070, this is going to be removed as
        # soon as the domain XML format supports the specification of USB
        # keyboards

        if self.arch == caps.Architecture.PPC64:
            domainAttrs['xmlns:qemu'] = \
                'http://libvirt.org/schemas/domain/qemu/1.0'

        self.dom = Element('domain', **domainAttrs)
        self.doc.appendChild(self.dom)

        self.dom.appendChildWithArgs('name', text=self.conf['vmName'])
        self.dom.appendChildWithArgs('uuid', text=self.conf['vmId'])
        memSizeKB = str(int(self.conf.get('memSize', '256')) * 1024)
        self.dom.appendChildWithArgs('memory', text=memSizeKB)
        self.dom.appendChildWithArgs('currentMemory', text=memSizeKB)
        vcpu = self.dom.appendChildWithArgs('vcpu', text=self._getMaxVCpus())
        vcpu.setAttrs(**{'current': self._getSmp()})

        memSizeGuaranteedKB = str(1024 * int(
            self.conf.get('memGuaranteedSize', '0')
        ))

        memtune = Element('memtune')
        self.dom.appendChild(memtune)

        memtune.appendChildWithArgs('min_guarantee',
                                    text=memSizeGuaranteedKB)

        self._devices = Element('devices')
        self.dom.appendChild(self._devices)

    def appendClock(self):
        """
        Add <clock> element to domain:

        <clock offset="variable" adjustment="-3600">
            <timer name="rtc" tickpolicy="catchup">
        </clock>
        """

        m = Element('clock', offset='variable',
                    adjustment=str(self.conf.get('timeOffset', 0)))
        rtc = m.appendChildWithArgs('timer', name='rtc', tickpolicy='catchup')
        if utils.tobool(self.conf.get('hypervEnable', 'false')):
            rtc.setAttrs(track='guest')
        m.appendChildWithArgs('timer', name='pit', tickpolicy='delay')

        if self.arch == caps.Architecture.X86_64:
            m.appendChildWithArgs('timer', name='hpet', present='no')

        self.dom.appendChild(m)

    def appendOs(self):
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
        """

        oselem = Element('os')
        self.dom.appendChild(oselem)

        DEFAULT_MACHINES = {caps.Architecture.X86_64: 'pc',
                            caps.Architecture.PPC64: 'pseries'}

        machine = self.conf.get('emulatedMachine', DEFAULT_MACHINES[self.arch])

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

        if self.arch == caps.Architecture.X86_64:
            oselem.appendChildWithArgs('smbios', mode='sysinfo')

        if utils.tobool(self.conf.get('bootMenuEnable', False)):
            oselem.appendChildWithArgs('bootmenu', enable='yes')

    def appendSysinfo(self, osname, osversion, serialNumber):
        """
        Add <sysinfo> element to domain:

        <sysinfo type="smbios">
          <bios>
            <entry name="vendor">QEmu/KVM</entry>
            <entry name="version">0.13</entry>
          </bios>
          <system>
            <entry name="manufacturer">Fedora</entry>
            <entry name="product">Virt-Manager</entry>
            <entry name="version">0.8.2-3.fc14</entry>
            <entry name="serial">32dfcb37-5af1-552b-357c-be8c3aa38310</entry>
            <entry name="uuid">c7a5fdbd-edaf-9455-926a-d65c16db1809</entry>
          </system>
        </sysinfo>
        """

        sysinfoelem = Element('sysinfo', type='smbios')
        self.dom.appendChild(sysinfoelem)

        syselem = Element('system')
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

        if (utils.tobool(self.conf.get('acpiEnable', 'true')) or
           utils.tobool(self.conf.get('hypervEnable', 'false'))):
            features = self.dom.appendChildWithArgs('features')

        if utils.tobool(self.conf.get('acpiEnable', 'true')):
            features.appendChildWithArgs('acpi')

        if utils.tobool(self.conf.get('hypervEnable', 'false')):
            hyperv = Element('hyperv')
            features.appendChild(hyperv)

            hyperv.appendChildWithArgs('relaxed', state='on')
            # turns off an internal Windows watchdog, and by doing so avoids
            # some high load BSODs.

    def appendCpu(self):
        """
        Add guest CPU definition.

        <cpu match="exact">
            <model>qemu64</model>
            <topology sockets="S" cores="C" threads="T"/>
            <feature policy="require" name="sse2"/>
            <feature policy="disable" name="svm"/>
        </cpu>
        """

        cpu = Element('cpu')

        if self.arch in (caps.Architecture.X86_64):
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
            cputune = Element('cputune')
            cpuPinning = self.conf.get('cpuPinning')
            for cpuPin in cpuPinning.keys():
                cputune.appendChildWithArgs('vcpupin', vcpu=cpuPin,
                                            cpuset=cpuPinning[cpuPin])
            self.dom.appendChild(cputune)

        # Guest numa topology support
        # see http://www.ovirt.org/Features/NUMA_and_Virtual_NUMA
        if 'guestNumaNodes' in self.conf:
            numa = Element('numa')
            guestNumaNodes = sorted(
                self.conf.get('guestNumaNodes'), key=itemgetter('nodeIndex'))
            for vmCell in guestNumaNodes:
                nodeMem = int(vmCell['memory']) * 1024
                numa.appendChildWithArgs('cell',
                                         cpus=vmCell['cpus'],
                                         memory=str(nodeMem))
            cpu.appendChild(numa)

        self.dom.appendChild(cpu)

    # Guest numatune support
    def appendNumaTune(self):
        """
        Add guest numatune definition.

        <numatune>
            <memory mode='strict' nodeset='0-1'/>
        </numatune>
        """

        if 'numaTune' in self.conf:
            numaTune = self.conf.get('numaTune')
            if 'nodeset' in numaTune.keys():
                mode = numaTune.get('mode', 'strict')
                numatune = Element('numatune')
                numatune.appendChildWithArgs('memory', mode=mode,
                                             nodeset=numaTune['nodeset'])
                self.dom.appendChild(numatune)

    def _appendAgentDevice(self, path, name):
        """
          <channel type='unix'>
             <target type='virtio' name='org.linux-kvm.port.0'/>
             <source mode='bind' path='/tmp/socket'/>
          </channel>
        """
        channel = Element('channel', type='unix')
        channel.appendChildWithArgs('target', type='virtio', name=name)
        channel.appendChildWithArgs('source', mode='bind', path=path)
        self._devices.appendChild(channel)

    def appendInput(self):
        """
        Add input device.

        <input bus="ps2" type="mouse"/>
        """
        if utils.tobool(self.conf.get('tabletEnable')):
            inputAttrs = {'type': 'tablet', 'bus': 'usb'}
        else:
            if self.arch == caps.Architecture.PPC64:
                mouseBus = 'usb'
            else:
                mouseBus = 'ps2'

            inputAttrs = {'type': 'mouse', 'bus': mouseBus}
        self._devices.appendChildWithArgs('input', **inputAttrs)

    def appendKeyboardDevice(self):
        """
        Add keyboard device for ppc64 using a QEMU argument directly.
        This is a workaround to the issue BZ#988070 in libvirt

            <qemu:commandline>
                <qemu:arg value='-usbdevice'/>
                <qemu:arg value='keyboard'/>
            </qemu:commandline>
        """
        commandLine = Element('qemu:commandline')
        commandLine.appendChildWithArgs('qemu:arg', value='-usbdevice')
        commandLine.appendChildWithArgs('qemu:arg', value='keyboard')
        self.dom.appendChild(commandLine)

    def appendEmulator(self):
        emulatorPath = '/usr/bin/qemu-system-' + self.arch

        emulator = Element('emulator', text=emulatorPath)

        self._devices.appendChild(emulator)

    def toxml(self):
        return self.doc.toprettyxml(encoding='utf-8')

    def _getSmp(self):
        return self.conf.get('smp', '1')

    def _getMaxVCpus(self):
        return self.conf.get('maxVCpus', self._getSmp())


if sys.version_info[:2] == (2, 6):
    # A little unrelated hack to make xml.dom.minidom.Document.toprettyxml()
    # not wrap Text node with whitespace.
    # reported upstream in http://bugs.python.org/issue4147
    # fixed in python 2.7 and python >= 3.2
    def __hacked_writexml(self, writer, indent="", addindent="", newl=""):

        # copied from xml.dom.minidom.Element.writexml and hacked not to wrap
        # Text nodes with whitespace.

        # indent = current indentation
        # addindent = indentation to add to higher levels
        # newl = newline string
        writer.write(indent + "<" + self.tagName)

        attrs = self._get_attributes()
        a_names = attrs.keys()
        a_names.sort()

        for a_name in a_names:
            writer.write(" %s=\"" % a_name)
            # _write_data(writer, attrs[a_name].value) # replaced
            xml.dom.minidom._write_data(writer, attrs[a_name].value)
            writer.write("\"")
        if self.childNodes:
            # added special handling of Text nodes
            if (len(self.childNodes) == 1 and
                    isinstance(self.childNodes[0], xml.dom.minidom.Text)):
                writer.write(">")
                self.childNodes[0].writexml(writer)
                writer.write("</%s>%s" % (self.tagName, newl))
            else:
                writer.write(">%s" % (newl))
                for node in self.childNodes:
                    node.writexml(writer, indent + addindent, addindent, newl)
                writer.write("%s</%s>%s" % (indent, self.tagName, newl))
        else:
            writer.write("/>%s" % (newl))

    xml.dom.minidom.Element.writexml = __hacked_writexml
