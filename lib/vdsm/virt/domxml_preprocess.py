#
# Copyright 2018 Red Hat, Inc.
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

"""
This module contains functions to preprocess the domain XML before submitting
it to libvirt to create the VM.

= Historical context

Up until Vdsm 4.20, Vdsm was in charge to create the domain XML, using the
configuration sent by Engine in custom, json-like format. The actual
configuration format was actually a semi-defined Python dictionary serialized
either to JSON or XML (even earlier versions of Vdsm).

Starting with oVirt 4.2, Vdsm 4.20, the clients, like Engine, are expected
to send the fully formed domain XML.

The end goal is that Vdsm just passes this XML through libvirt; once libvirt
starts the VM, Vdsm will read back the up to date XML and will initialize
its data structures, needed to support all the flows and the API verbs.

However, Vdsm 4.20 can't implement the final goal for various reasons, most
important among them is the amount of legacy code which needs to be gradually
replaced, and the need to support per-host hooks, which may alter the XML -
see the localdisk hook for a prime example.
Storage devices may also need to change the XML.

= Affected flows

Two creation flows need to alter the domain XML, thus require the support of
the functionality of this module.

== Creation flow

In the creation flow, we need to handle
- placeholders. Engine may not know everything about the domain, or anyway
  intend to demand Vdsm some data. Examples are drive leases, or smbios
  settings.
  This is data that is relevent on per-host basis, and that either is not
  easily accessible by Engine, or that is actually more up to date on the
  host.
- hooks. Hooks may need to replace some parts of the XML.
- storage devices. We need to update their XML snippets with host-specific
  information. This requirement is expected to be lifted during the oVirt
  4.3 development cycle, but it still holds now.

== De-hibernation (aka restore state) flow

When Engine restarts a hibernated VM, it may change the volume chains
in the storage devices.
This may happen if some snapshots of the VMs where previewed and now
committed.
Vdsm needs to amend the restored XML to use those new leaf nodes.

= Placeholders

Starting with version 4.2, Engine may send in the domain XML special values
for VM-specific data it doesn't know, or that it know it is more updated
on the host when the VM is started.
A domain XML with placeholders must be syntactically valid, even though
the values which are actually placeholders will make no sense (e.g.
specifying one offset with the string OFFSET rather than with one unsigned
integer).

Vdsm will replace those special values with actual data.
Please check the documentation of the functions in this module to learn
about the supported placeholders and their meaning.
"""

from vdsm.common import cpuarch
from vdsm import constants
from vdsm import host
from vdsm import osinfo

from vdsm.virt import vmdevices


def replace_placeholders(xml_str, cif, arch, serial, devices):
    """
    Replace the placeholders, if any, in the domain XML.
    This is the entry point orchestration function.
    See the documentation of the specific functions
    for the supported placeholders.
    """

    xml_str = vmdevices.graphics.fixDisplayNetworks(xml_str)

    xml_str = vmdevices.lease.fixLeases(
        cif.irs, xml_str, devices.get(vmdevices.hwclass.DISK, []))

    xml_str = vmdevices.network.fixNetworks(xml_str)

    if cpuarch.is_x86(arch):
        osd = osinfo.version()
        os_version = osd.get('version', '') + '-' + osd.get('release', '')
        serial_number = host.uuid() if serial is None else serial
        xml_str = xml_str.replace('OS-NAME:', constants.SMBIOS_OSNAME)
        xml_str = xml_str.replace('OS-VERSION:', os_version)
        xml_str = xml_str.replace('HOST-SERIAL:', serial_number)

    return xml_str
