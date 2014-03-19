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
import xml.dom.minidom


class DomainDescriptor(object):

    def __init__(self, xmlStr):
        self._xml = xmlStr
        self._dom = xml.dom.minidom.parseString(xmlStr)
        self._devices = self._firstElementByTagName('devices')
        # VDSM by default reports '0' as hash when it has no device list yet
        self._devicesHash = hash(self._devices) if self._devices else 0

    @classmethod
    def fromId(cls, uuid):
        return cls('<domain><uuid>%s</uuid></domain>' % uuid)

    @property
    def xml(self):
        return self._xml

    @property
    def dom(self):
        return self._dom

    @property
    def devices(self):
        return self._devices

    def getDeviceElements(self, tagName):
        return self._devices.getElementsByTagName(tagName)

    @property
    def devicesHash(self):
        return self._devicesHash

    def _firstElementByTagName(self, tagName):
        elements = self._dom.childNodes[0].getElementsByTagName(tagName)
        return elements[0] if elements else None
