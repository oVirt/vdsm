#
# Copyright 2015-2016 Red Hat, Inc.
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

import os
import xml.etree.ElementTree as etree

import libvirt


def Error(code, msg="fake error"):
    e = libvirt.libvirtError(msg)
    e.err = [code, None, msg]
    return e


class Connection(object):

    def __init__(self, *args):
        self.secrets = {}

    def secretDefineXML(self, xml):
        uuid, usage_type, usage_id, description = parse_secret(xml)
        if uuid in self.secrets:
            # If a secret exists, we cannot change its usage_id
            # See libvirt/src/secret/secret_driver.c:782
            sec = self.secrets[uuid]
            if usage_id != sec.usage_id:
                raise Error(libvirt.VIR_ERR_INTERNAL_ERROR)
            sec.usage_type = usage_type
            sec.description = description
        else:
            # (usage_type, usage_id) pair must be unique
            for sec in list(self.secrets.values()):
                if sec.usage_type == usage_type and sec.usage_id == usage_id:
                    raise Error(libvirt.VIR_ERR_INTERNAL_ERROR)
            sec = Secret(self, uuid, usage_type, usage_id, description)
            self.secrets[uuid] = sec
        return sec

    def secretLookupByUUIDString(self, uuid):
        if uuid not in self.secrets:
            raise Error(libvirt.VIR_ERR_NO_SECRET)
        return self.secrets[uuid]

    def listAllSecrets(self, flags=0):
        return list(self.secrets.values())

    def domainEventRegisterAny(self, *arg):
        pass

    def listAllNetworks(self, *args):
        return []

    def nodeDeviceLookupByName(self, name):
        """
        This is a method that allows us to access hostdev XML in a test.
        Normally, libvirt holds the device XML but in case of unit testing,
        we cannot access the libvirt.

        If we want to use hostdev in a test, the XML itself must be supplied
        in tests/devices/data/${device address passed}.
        """
        fakelib_path = os.path.realpath(__file__)
        dir_name = os.path.split(fakelib_path)[0]
        xml_path = os.path.join(
            dir_name, 'devices', 'data', name + '.xml')

        device_xml = None
        with open(xml_path, 'r') as device_xml_file:
            device_xml = device_xml_file.read()

        return VirNodeDeviceStub(device_xml)


class Secret(object):

    def __init__(self, con, uuid, usage_type, usage_id, description):
        self.con = con
        self.uuid = uuid
        self.usage_type = usage_type
        self.usage_id = usage_id
        self.description = description
        self.value = None

    def undefine(self):
        del self.con.secrets[self.uuid]

    def UUIDString(self):
        return self.uuid

    def usageID(self):
        return self.usage_id

    def setValue(self, value):
        self.value = value


class VirNodeDeviceStub(object):

    def __init__(self, xml):
        self.xml = xml
        xml_processed = etree.fromstring(self.XMLDesc(0).decode(
            'ascii', errors='ignore'))
        self._name = xml_processed.find('name').text
        self.capability = xml_processed.find('capability').attrib['type']

    def XMLDesc(self, flags=0):
        return self.xml

    def name(self):
        return self._name

    # unfortunately, in real environment these are the most problematic calls
    # but in order to test them, we would put host in danger of removing
    # device needed to run properly (such as nic)

    # the name dettach is defined like *this* in libvirt API, known mistake
    def dettach(self):
        pass

    def reAttach(self):
        pass


def parse_secret(xml):
    root = etree.fromstring(xml)
    uuid = root.find("./uuid").text
    usage_type = root.find("./usage/[@type]").get("type")
    if usage_type == "volume":
        usage_id = root.find("./usage/volume").text
    elif usage_type == "ceph":
        usage_id = root.find("./usage/name").text
    elif usage_type == "iscsi":
        usage_id = root.find("./usage/target").text
    else:
        raise Error(libvirt.VIR_ERR_INTERNAL_ERROR)
    try:
        description = root.find("./description").text
    except AttributeError:
        description = None
    return uuid, usage_type, usage_id, description
