#
# Copyright 2008-2016 Red Hat, Inc.
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

import copy
from operator import itemgetter
import xml.etree.ElementTree as etree

from vdsm.common import xmlutils
from vdsm.virt import xmlconstants
from vdsm import constants
from vdsm import cpuarch
from vdsm import utils

METADATA_VM_TUNE_URI = 'http://ovirt.org/vm/tune/1.0'
METADATA_VM_TUNE_ELEMENT = 'qos'
METADATA_VM_TUNE_PREFIX = 'ovirt'

_BOOT_MENU_TIMEOUT = 10000  # milliseconds

_UNSPECIFIED = object()


class NotFound(Exception):
    """
    Raised when vmxml helpers can't find some requested entity.
    """
    pass


def parse_xml(xml_string):
    """
    Parse given XML string to DOM element and return the element.

    :param xml_string: XML string to parse
    :type xml_string: str
    :returns: DOM element created by parsing `xml_string`
    :rtype: DOM element
    """
    return etree.fromstring(xml_string)


def format_xml(element, pretty=False):
    """
    Export given DOM element to XML string.

    :param element: DOM element to export
    :type element: DOM element
    :param pretty: whether to make the output more human readable
    :type pretty: boolean
    :returns: XML corresponding to `element` content
    :rtype: string
    """
    if pretty:
        element = copy.deepcopy(element)
        xmlutils.indent(element, 0)
    return etree.tostring(element, encoding='UTF-8')


def find_all(element, tag_):
    """
    Return an iterator over all DOM elements with given `tag`.

    `element` may is included in the result if it is of `tag`.

    :param element: DOM element to be searched for given `tag`
    :type element: DOM element
    :param tag: tag to look for
    :type tag: basestring
    :returns: all elements with given `tag`
    :rtype: sequence of DOM elements
    """
    if tag(element) == tag_:
        yield element
    for elt in element.findall('.//' + tag_):
        yield elt


def find_first(element, tag, default=_UNSPECIFIED):
    """
    Find the first DOM element with the given tag.

    :param element: DOM element to be searched for given `tag`
    :type element: DOM element
    :param tag: tag to look for
    :type tag: basestring
    :param default: any object to return when no element with `tag` is found;
      if not given then `NotFound` is raised in such a case
    :returns: the matching element or default
    :raises: NotFound -- when no element with `tag` is found and `default` is
      not given
    """
    try:
        return next(find_all(element, tag))
    except StopIteration:
        if default is _UNSPECIFIED:
            raise NotFound((element, tag,))
        else:
            return default


def find_attr(element, tag, attribute):
    """
    Find `attribute` value of the first DOM element with `tag`.

    :param element: DOM element to be searched for given `tag`
    :type element: DOM element
    :param tag: tag to look for
    :type tag: basestring
    :param attribute: attribute name to look for
    :type attribute: basestring
    :returns: the attribute value or an empty string if no element with given
      tag is found or the found element doesn't contain `attribute`
    :rtype: basestring
    """
    try:
        subelement = find_first(element, tag)
    except NotFound:
        return ''
    return attr(subelement, attribute)


def tag(element):
    """
    Return tag of the given DOM element.

    :param element: element to get the tag of
    :type element: DOM element
    :returns: tag of the element
    :rtype: basestring
    """
    return element.tag


def attr(element, attribute):
    """
    Return attribute values of `element`.

    :param element: the element to look the attributes in
    :type element: DOM element
    :param attribute: attribute name to look for
    :type attribute: basestring
    :returns: the corresponding attribute value or empty string (if `attribute`
      is not present)
    :rtype: basestring
    """
    # etree returns unicodes, except for empty strings.
    return element.get(attribute, '')


def attributes(element):
    """
    Return dictionary of all the `element` attributes.

    :param element: the element to look the attributes in
    :type element: DOM element
    :returns: dictionary of attribute names (basestrings) and their values
      (basestrings)
    :rtype: dictionary
    """
    return {a: attr(element, a) for a in element.keys()}


def set_attr(element, attribute, value):
    """
    Set `attribute` of `element` to `value`.

    :param element: the element to change the attribute in
    :type element: DOM element
    :param attribute: attribute name
    :type attribute: basestring
    :param value: new value of the attribute
    :type value: basestring
    """
    element.set(attribute, value)


def text(element):
    """
    Return text of the given DOM element.

    :param element: element to get the text from
    :type element: DOM element
    :returns: text of the element (empty string if it the element doesn't
      contain any text)
    :rtype: basestring
    """
    return element.text or ''


def children(element, tag=None):
    """
    Return direct subelements of `element`.

    :param element: element to get the children from
    :type element: DOM element
    :param tag: if given then only children with this tag are returned
    :type tag: basestring
    :returns: children of `element`, optionally filtered by `tag`
    :rtype: iterator providing the selected children

    """
    if tag is None:
        return iter(element)
    else:
        return element.iterfind('./' + tag)


def append_child(element, child):
    """
    Add child element to `element`.

    :param element: element to add the child to
    :type element: DOM element
    :param child: child element to add to `element`
    :type child: DOM element

    """
    element.append(child)


def remove_child(element, child):
    """
    Remove child element from `element`.

    :param element: element to add the child to
    :type element: DOM element
    :param child: child element to remove from `element`
    :type child: DOM element

    """
    element.remove(child)


def has_channel(domXML, name):
    domObj = etree.fromstring(domXML)
    devices = domObj.findall('devices')

    if len(devices) == 1:
        for chan in devices[0].findall('channel'):
            targets = chan.findall('target')
            if len(targets) == 1:
                if targets[0].attrib['name'] == name:
                    return True

    return False


def device_address(device_xml, index=0):
    """
    Obtain device's address from libvirt
    """
    address = {}
    address_element = list(find_all(device_xml, 'address'))[index]
    # Parse address to create proper dictionary.
    # Libvirt device's address definition is:
    # PCI = {'type':'pci', 'domain':'0x0000', 'bus':'0x00',
    #        'slot':'0x0c', 'function':'0x0'}
    # IDE = {'type':'drive', 'controller':'0', 'bus':'0', 'unit':'0'}
    for key, value in attributes(address_element).iteritems():
        address[key.strip()] = value.strip()
    return address


class Device(object):
    # since we're inheriting all VM devices from this class, __slots__ must
    # be initialized here in order to avoid __dict__ creation
    __slots__ = ()

    def createXmlElem(self, elemType, deviceType, attributes=()):
        """
        Create domxml device element according to passed in params
        """
        elemAttrs = {}
        element = Element(elemType)

        if deviceType:
            elemAttrs['type'] = deviceType

        for attrName in attributes:
            if not hasattr(self, attrName):
                continue

            attr = getattr(self, attrName)
            if isinstance(attr, dict):
                element.appendChildWithArgs(attrName, **attr)
            else:
                elemAttrs[attrName] = attr

        element.setAttrs(**elemAttrs)
        return element


class Element(object):

    def __init__(self, tagName, text=None, namespace=None, namespace_uri=None,
                 **attrs):
        if namespace_uri is not None:
            tagName = '{%s}%s' % (namespace_uri, tagName,)
            if namespace is not None:
                etree.register_namespace(namespace, namespace_uri)
        self._elem = etree.Element(tagName)
        self.setAttrs(**attrs)
        if text is not None:
            self.appendTextNode(text)

    def __getattr__(self, name):
        return getattr(self._elem, name)

    def __len__(self):
        return len(self._elem)

    def __iter__(self):
        return iter(self._elem)

    def setAttrs(self, **attrs):
        for attrName, attrValue in attrs.iteritems():
            self._elem.set(attrName, attrValue)

    def setAttr(self, attrName, attrValue):
        self._elem.set(attrName, attrValue)

    def appendTextNode(self, text):
        self._elem.text = text

    def appendChild(self, element):
        self._elem.append(element)

    def appendChildWithArgs(self, childName, text=None, **attrs):
        child = Element(childName, text, **attrs)
        self._elem.append(child)
        return child

    def replaceChild(self, new_element, old_element):
        for c in list(children(self._elem)):
            if c is old_element or \
               (isinstance(c, Element) and c._elem is old_element):
                self._elem.remove(c)
                break
        else:
            raise NotFound(old_element)
        self._elem.append(new_element)


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

        if utils.tobool(self.conf.get('kvmEnable', 'true')):
            domainType = 'kvm'
        else:
            domainType = 'qemu'

        domainAttrs = {'type': domainType}

        self.dom = Element('domain', **domainAttrs)

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

        self._devices = Element('devices')
        self.dom.appendChild(self._devices)

        self.appendMetadata()

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

        m = Element('clock', offset='variable',
                    adjustment=str(self.conf.get('timeOffset', 0)))
        if utils.tobool(self.conf.get('hypervEnable', 'false')):
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

        metadata = Element('metadata')
        self._appendMetadataQOS(metadata)
        self._appendMetadataContainer(metadata)
        self.dom.appendChild(metadata)

    def _appendMetadataQOS(self, metadata):
        metadata.appendChild(Element(METADATA_VM_TUNE_ELEMENT,
                                     namespace=METADATA_VM_TUNE_PREFIX,
                                     namespace_uri=METADATA_VM_TUNE_URI))

    def _appendMetadataContainer(self, metadata):
        custom = self.conf.get('custom', {})
        # container{Type,Image} are mandatory: if either
        # one is missing, no container-related extradata
        # should be present at all.
        container_type = custom.get('containerType')
        container_image = custom.get('containerImage')
        if not container_type or not container_image:
            return

        cont = Element(
            xmlconstants.METADATA_CONTAINERS_ELEMENT,
            namespace=xmlconstants.METADATA_CONTAINERS_PREFIX,
            namespace_uri=xmlconstants.METADATA_CONTAINERS_URI,
            image=container_image,
            text=container_type
        )

        metadata.appendChild(cont)

        # drive mapping is optional. It is totally fine for a container
        # not to use any drive, this just means it will not have any
        # form of persistency.
        drive_map = parse_drive_mapping(self.conf.get('custom', {}))
        if drive_map:
            dm = Element(
                xmlconstants.METADATA_VM_DRIVE_MAP_ELEMENT,
                namespace=xmlconstants.METADATA_VM_DRIVE_MAP_PREFIX,
                namespace_uri=xmlconstants.METADATA_VM_DRIVE_MAP_URI
            )

            for name, drive in drive_map.items():
                vol = Element(
                    xmlconstants.METADATA_VM_DRIVE_VOLUME_ELEMENT,
                    namespace=xmlconstants.METADATA_VM_DRIVE_MAP_PREFIX,
                    namespace_uri=xmlconstants.METADATA_VM_DRIVE_MAP_URI,
                    name=name,
                    drive=drive)
                dm.appendChild(vol)
            metadata.appendChild(dm)

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

        oselem = Element('os')
        self.dom.appendChild(oselem)

        DEFAULT_MACHINES = {cpuarch.X86_64: 'pc',
                            cpuarch.PPC64: 'pseries',
                            cpuarch.PPC64LE: 'pseries'}

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

        if cpuarch.is_x86(self.arch):
            oselem.appendChildWithArgs('smbios', mode='sysinfo')

        if utils.tobool(self.conf.get('bootMenuEnable', False)):
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
            hyperv.appendChildWithArgs('vapic', state='on')
            # magic number taken from recomendations. References:
            # https://bugzilla.redhat.com/show_bug.cgi?id=1083529#c10
            # https://bugzilla.redhat.com/show_bug.cgi?id=1053846#c0
            hyperv.appendChildWithArgs(
                'spinlocks', state='on', retries='8191')

    def appendCpu(self):
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

        cpu = Element('cpu')

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
            <memnode cellid='0' mode='strict' nodeset='1'>
        </numatune>
        """

        numaTune = self.conf.get('numaTune')

        numatune = Element('numatune')
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

        numatune = Element('numatune')
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
        elif cpuarch.is_x86(self.arch):
            inputAttrs = {'type': 'mouse', 'bus': 'ps2'}
        else:
            inputAttrs = {'type': 'mouse', 'bus': 'usb'}

        self._devices.appendChildWithArgs('input', **inputAttrs)

    def appendEmulator(self):
        emulatorPath = '/usr/bin/qemu-system-' + self.arch

        emulator = Element('emulator', text=emulatorPath)

        self._devices.appendChild(emulator)

    def appendDeviceXML(self, deviceXML):
        self._devices.appendChild(parse_xml(deviceXML))

    def toxml(self):
        return format_xml(self.dom, pretty=True)

    def _getSmp(self):
        return self.conf.get('smp', '1')

    def _getMaxVCpus(self):
        return self.conf.get('maxVCpus', self._getSmp())


def parse_drive_mapping(custom):
    mappings = custom.get('volumeMap', None)
    if mappings is None:
        return {}

    drive_mapping = {}
    for mapping in mappings.split(','):
        name, drive = mapping.strip().split(':', 1)
        drive_mapping[name.strip()] = drive.strip()
    return drive_mapping
