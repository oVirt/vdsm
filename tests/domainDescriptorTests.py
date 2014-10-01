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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA
# 02110-1301  USA
#
# Refer to the README and COPYING files for full details of the license
#

from virt.domain_descriptor import DomainDescriptor
from testlib import VdsmTestCase

NO_DEVICES = """
<domain>
    <uuid>xyz</uuid>
</domain>
"""

EMPTY_DEVICES = """
<domain>
    <uuid>xyz</uuid>
    <devices/>
</domain>
"""

SOME_DEVICES = """
<domain>
    <uuid>xyz</uuid>
    <devices>
        <device name="foo"/>
        <device name="bar"/>
    </devices>
</domain>
"""

REORDERED_DEVICES = """
<domain>
    <uuid>xyz</uuid>
    <devices>
        <device name="bar"/>
        <device name="foo"/>
    </devices>
</domain>
"""


class DevicesHashTests(VdsmTestCase):

    def test_no_devices(self):
        desc1 = DomainDescriptor(NO_DEVICES)
        desc2 = DomainDescriptor(EMPTY_DEVICES)
        self.assertNotEqual(desc1.devices_hash, desc2.devices_hash)

    def test_different_devices(self):
        desc1 = DomainDescriptor(EMPTY_DEVICES)
        desc2 = DomainDescriptor(SOME_DEVICES)
        self.assertNotEqual(desc1.devices_hash, desc2.devices_hash)

    def test_different_order(self):
        desc1 = DomainDescriptor(SOME_DEVICES)
        desc2 = DomainDescriptor(REORDERED_DEVICES)
        self.assertNotEqual(desc1.devices_hash, desc2.devices_hash)

    def test_stable_hash(self):
        desc1 = DomainDescriptor(SOME_DEVICES)
        desc2 = DomainDescriptor(SOME_DEVICES)
        self.assertEqual(desc1.devices_hash, desc2.devices_hash)
