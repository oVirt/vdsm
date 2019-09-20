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
from __future__ import division

import unittest

from network.nettestlib import dummy_device, vlan_device

from vdsm.network.link import vlan


class LinkIfaceTests(unittest.TestCase):
    def test_list_vlans_on_base_device(self):
        with dummy_device() as nic:
            with vlan_device(nic, tag=999) as vlan_dev:
                self.assertEqual(
                    [vlan_dev.devName],
                    list(vlan.get_vlans_on_base_device(nic)),
                )

    def test_identify_vlan_base_device(self):
        with dummy_device() as nic:
            with vlan_device(nic, tag=999):
                self.assertTrue(vlan.is_base_device(nic))

    def test_identify_non_vlan_base_device(self):
        with dummy_device() as nic:
            self.assertFalse(vlan.is_base_device(nic))
