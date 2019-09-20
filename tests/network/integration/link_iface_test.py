# Copyright 2016-2018 Red Hat, Inc.
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

from network.nettestlib import dummy_device

from vdsm.network.link.iface import iface


class LinkIfaceTests(unittest.TestCase):
    def test_iface_up(self):
        with dummy_device() as nic:
            _iface = iface(nic)
            _iface.up()
            self.assertTrue(_iface.is_up())

    def test_iface_down(self):
        with dummy_device() as nic:
            _iface = iface(nic)
            _iface.up()
            _iface.down()
            self.assertFalse(_iface.is_up())

    def test_iface_notpromisc(self):
        with dummy_device() as nic:
            _iface = iface(nic)
            _iface.up()
            self.assertFalse(_iface.is_promisc())

    def test_iface_hwaddr(self):
        MAC_ADDR = '02:00:00:00:00:01'

        with dummy_device() as nic:
            _iface = iface(nic)
            _iface.set_address(MAC_ADDR)
            self.assertEqual(MAC_ADDR, _iface.address())
