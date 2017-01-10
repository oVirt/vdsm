#
# Copyright 2014 Red Hat, Inc.
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
from . import vmxml


class MutableDomainDescriptor(object):

    def __init__(self, xmlStr):
        self._dom = vmxml.parse_xml(xmlStr)

    @classmethod
    def from_id(cls, uuid):
        return cls('<domain><uuid>%s</uuid></domain>' % uuid)

    @property
    def xml(self):
        return vmxml.format_xml(self._dom)

    @property
    def devices(self):
        return vmxml.find_first(self._dom, 'devices', None)

    def get_device_elements(self, tagName):
        return vmxml.find_all(self.devices, tagName)

    @property
    def devices_hash(self):
        devices = self.devices
        return hash(vmxml.format_xml(devices) if devices is not None else '')

    def all_channels(self):
        for channel in vmxml.find_all(self.devices, 'channel'):
            name = vmxml.find_attr(channel, 'target', 'name')
            path = vmxml.find_attr(channel, 'source', 'path')
            if name and path:
                yield name, path

    def get_memory_size(self):
        """
        Return the vm memory from xml in MiB
        """
        memory = vmxml.find_first(self._dom, "memory", None)
        return int(vmxml.text(memory)) // 1024 if memory is not None else None


class DomainDescriptor(MutableDomainDescriptor):

    def __init__(self, xmlStr):
        super(DomainDescriptor, self).__init__(xmlStr)
        self._xml = xmlStr
        self._devices = super(DomainDescriptor, self).devices
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
