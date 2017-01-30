# Copyright 2016-2017 Red Hat, Inc.
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

from nose.plugins.attrib import attr

from testlib import VdsmTestCase as TestCaseBase

from .nettestlib import dummy_device

from vdsm.network.link import iface


@attr(type='integration')
class LinkIfaceTests(TestCaseBase):

    def test_iface_up(self):
        with dummy_device() as nic:
            iface.up(nic)
            self.assertTrue(iface.is_up(nic))

    def test_iface_down(self):
        with dummy_device() as nic:
            iface.up(nic)
            iface.down(nic)
            self.assertFalse(iface.is_up(nic))

    def test_iface_notpromisc(self):
        with dummy_device() as nic:
            iface.up(nic)
            self.assertFalse(iface.is_promisc(nic))

    def test_iface_hwaddr(self):
        MAC_ADDR = '02:00:00:00:00:01'

        with dummy_device() as nic:
            iface.set_mac_address(nic, MAC_ADDR)
            self.assertEqual(MAC_ADDR, iface.mac_address(nic))
