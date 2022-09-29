# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

from contextlib import contextmanager
import enum
import xml.etree.ElementTree as etree

from vdsm import taskset
from vdsm.common import xmlutils
from vdsm.virt import metadata
from vdsm.virt import vmxml


class XmlSource(enum.Enum):
    INITIAL = enum.auto()
    MIGRATION_SOURCE = enum.auto()
    LIBVIRT = enum.auto()


class MutableDomainDescriptor(object):

    def __init__(self, xmlStr):
        self._dom = xmlutils.fromstring(xmlStr)
        self._id = self._dom.findtext('uuid')
        self._name = self._dom.findtext('name')

    @classmethod
    def from_id(cls, uuid):
        return cls('<domain><uuid>%s</uuid></domain>' % uuid)

    @property
    def metadata(self):
        return vmxml.find_first(self._dom, 'metadata', None)

    @property
    def xml(self):
        return xmlutils.tostring(self._dom, pretty=True)

    @property
    def id(self):
        return self._id

    @property
    def name(self):
        return self._name

    def vm_type(self):
        return self._dom.get('type', '')

    def acpi_enabled(self):
        return self._dom.find('features/acpi') is not None

    @property
    def devices(self):
        return vmxml.find_first(self._dom, 'devices', None)

    def get_device_elements(self, tagName):
        return vmxml.find_all(self.devices, tagName)

    def get_device_elements_with_attrs(self, tag_name, **kwargs):
        for element in vmxml.find_all(self.devices, tag_name):
            if all(vmxml.attr(element, key) == value
                    for key, value in kwargs.items()):
                yield element

    @contextmanager
    def metadata_descriptor(self):
        md_desc = metadata.Descriptor.from_tree(self._dom)
        yield md_desc
        old_md = vmxml.find_first(self._dom, 'metadata', None)
        if old_md is not None:
            vmxml.remove_child(self._dom, old_md)
        md_elem = etree.Element('metadata')
        vmxml.append_child(self._dom, etree_child=md_elem)
        vmxml.append_child(md_elem, etree_child=md_desc.to_tree())

    @property
    def devices_hash(self):
        devices = self.devices
        return hash(xmlutils.tostring(devices) if devices is not None else '')

    def all_channels(self):
        """
        Returns a tuple (name, path, state) for each channel device in domain
        XML. Name and path are always non-empty strings, state is non-empty
        string (connected/disconnected) or None if the channel state is
        unknown.
        """
        if self.devices is not None:
            for channel in vmxml.find_all(self.devices, 'channel'):
                name = vmxml.find_attr(channel, 'target', 'name')
                path = vmxml.find_attr(channel, 'source', 'path')
                state = vmxml.find_attr(channel, 'target', 'state')
                if name and path:
                    yield name, path, (None if not state else state)

    def get_number_of_cpus(self):
        """
        Return the number of VM's CPUs as int.
        """
        vcpu = self._dom.find('./vcpu')
        if vcpu is None:
            raise LookupError('Element vcpu not found in domain XML')
        cpus = vmxml.attr(vcpu, 'current')
        if cpus == '':
            # If attribute current is not present fall-back to element text
            cpus = vcpu.text
        return int(cpus)

    def get_memory_size(self, current=False):
        """
        Return the vm memory from xml in MiB.

        :param current: If true, return current memory size (which may be
          reduced by balloon); if false, return boot time memory size.
        :type current: bool
        """
        tag = 'currentMemory' if current else 'memory'
        memory = vmxml.find_first(self._dom, tag, None)
        return int(vmxml.text(memory)) // 1024 if memory is not None else None

    def on_reboot_config(self):
        """
        :return: The value of <on_reboot> element, if it exists.
        """
        elem = next((el for el in self._dom.findall('.//on_reboot')), None)
        return elem is not None and elem.text or None

    @property
    def nvram(self):
        """
        :return: NVRAM element defining NVRAM store (used to store UEFI or
          SecureBoot variables) or None if the VM has no NVRAM store.
        """
        return vmxml.find_first(self._dom, 'os/nvram', None)

    @property
    def pinned_cpus(self):
        """
        :return: A dictionary in which key is vCPU ID and value is a frozenset
          with IDs of pCPUs the vCPU is pinned to. If a vCPU is not pinned to
          any pCPU it is not listed in the dictionary. Empty dictionary is
          returned if none of the vCPUs has a pinning defined.
        """
        cputune = vmxml.find_first(self._dom, 'cputune', None)
        if cputune is None:
            return {}
        pinning = dict()
        for vcpupin in vmxml.find_all(cputune, 'vcpupin'):
            cpuset = vcpupin.get('cpuset', None)
            vcpu = vcpupin.get('vcpu', None)
            if vcpu is not None and cpuset is not None:
                cpus = taskset.cpulist_parse(cpuset)
                if len(cpus) > 0:
                    pinning[int(vcpu)] = cpus
        return pinning

    @property
    def vnuma_count(self):
        """
        :return: Number of vNUMA cells defined in VM. Zero is returned when
          NUMA is not defined.
        """
        numa = vmxml.find_first(self._dom, 'cpu/numa', None)
        if numa is None:
            return 0
        return len(list(vmxml.find_all(numa, 'cell')))


class DomainDescriptor(MutableDomainDescriptor):

    def __init__(self, xmlStr, xml_source=XmlSource.LIBVIRT):
        """
        :param xmlStr: Domain XML
        :type xmlStr: string
        :param xml_source: If set to INITIAL or MIGRATION_SOURCE then the
          provided domain XML is the initial domain XML and possitbly not yet
          filled with information from libvirt, such as device addresses.
          Device hash is None in such a case, to prevent Engine from
          retrieving and processing incomplete device information.
        :type xml_source: XmlSource
        :type migration_src: bool
        """
        super(DomainDescriptor, self).__init__(xmlStr)
        self._xml = xmlStr
        self._xml_source = xml_source
        self._devices = super(DomainDescriptor, self).devices
        if self._xml_source == XmlSource.INITIAL or \
                self._xml_source == XmlSource.MIGRATION_SOURCE:
            self._devices_hash = None
        else:
            self._devices_hash = super(DomainDescriptor, self).devices_hash

    @property
    def xml_source(self):
        return self._xml_source

    @property
    def xml(self):
        return self._xml

    @property
    def devices(self):
        return self._devices

    @property
    def devices_hash(self):
        return self._devices_hash

    @contextmanager
    def metadata_descriptor(self):
        yield metadata.Descriptor.from_tree(self._dom)
