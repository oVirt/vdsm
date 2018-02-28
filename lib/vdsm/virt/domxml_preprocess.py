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
"""
