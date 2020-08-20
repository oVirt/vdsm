#
# Copyright 2014-2020 Red Hat, Inc.
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
from __future__ import division

from contextlib import contextmanager
import xml.etree.ElementTree as etree

from vdsm.common import xmlutils
from vdsm.virt import metadata
from vdsm.virt import vmxml


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
        if self.devices is not None:
            for channel in vmxml.find_all(self.devices, 'channel'):
                name = vmxml.find_attr(channel, 'target', 'name')
                path = vmxml.find_attr(channel, 'source', 'path')
                if name and path:
                    yield name, path

    def get_number_of_cpus(self):
        """
        Return the number of VM's CPUs as a string.
        """
        return self._dom.findtext('vcpu')

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


class DomainDescriptor(MutableDomainDescriptor):

    def __init__(self, xmlStr, initial=False):
        """
        :param xmlStr: Domain XML
        :type xmlStr: string
        :param initial: Iff true then the provided domain XML is
          the initial domain XML provided by Engine and not yet filled
          with information from libvirt, such as device addresses.
          Device hash is None in such a case, to prevent Engine from
          retrieving and processing incomplete device information.
        :type initial: bool
        """
        super(DomainDescriptor, self).__init__(xmlStr)
        self._xml = xmlStr
        self._initial = initial
        self._devices = super(DomainDescriptor, self).devices
        if self._initial:
            self._devices_hash = None
        else:
            self._devices_hash = super(DomainDescriptor, self).devices_hash

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
