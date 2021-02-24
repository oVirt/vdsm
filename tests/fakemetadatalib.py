#
# Copyright 2017 Red Hat, Inc.
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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#

from __future__ import absolute_import
from __future__ import division

import libvirt

from vdsm.virt.domain_descriptor import DomainDescriptor
from vdsm.virt import metadata
from vdsm.virt import xmlconstants

from vmfakecon import Error


BLANK_UUID = '00000000-0000-0000-0000-000000000000'


MINIMAL_DOM_XML = u"""<?xml version="1.0" encoding="utf-8"?>
<domain type="kvm" xmlns:ovirt-vm="http://ovirt.org/vm/1.0">
  <uuid>{uuid}</uuid>
  <metadata />
</domain>""".format(uuid=BLANK_UUID)


def setup_vm(vm):
    vm.conf['xml'] = MINIMAL_DOM_XML
    # needed by _init_from_metadata
    vm._external = False
    vm._exit_info = {}
    vm._domain = DomainDescriptor(vm.conf['xml'])
    # now the real metadata section
    vm._md_desc = metadata.Descriptor.from_xml(vm.conf['xml'])
    vm._init_from_metadata()


class FakeDomain(object):

    @classmethod
    def with_metadata(
        cls,
        xml_string,
        prefix=xmlconstants.METADATA_VM_VDSM_PREFIX,
        uri=xmlconstants.METADATA_VM_VDSM_URI
    ):
        dom = cls()
        if xml_string:
            dom.setMetadata(
                libvirt.VIR_DOMAIN_METADATA_ELEMENT,
                xml_string, prefix, uri,
            )
        return dom

    def __init__(self, vmid=BLANK_UUID):
        self.xml = {}
        self._uuid = vmid

    def UUIDString(self):
        return self._uuid

    def metadata(self, xml_type, uri, flags=0):
        # we only support METADATA_ELEMENT
        assert xml_type == libvirt.VIR_DOMAIN_METADATA_ELEMENT
        xml_string = self.xml.get(uri, None)
        if xml_string is None:
            raise Error(libvirt.VIR_ERR_NO_DOMAIN_METADATA)
        return xml_string

    def setMetadata(self, xml_type, xml_string, prefix, uri, flags=0):
        self.xml[uri] = xml_string
