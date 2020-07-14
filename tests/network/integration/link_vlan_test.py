# Copyright 2018-2020 Red Hat, Inc.
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

import pytest

from network.nettestlib import dummy_device, vlan_device

from vdsm.network.link import vlan


@pytest.fixture
def vlan999(nic0):
    with vlan_device(nic0, tag=999) as vlan_dev:
        yield vlan_dev


@pytest.fixture
def nic0():
    with dummy_device() as nic:
        yield nic


class TestLinkIface(object):
    def test_list_vlans_on_base_device(self, nic0, vlan999):
        base_device_vlans = list(vlan.get_vlans_on_base_device(nic0))
        assert base_device_vlans == [vlan999.devName]

    def test_identify_vlan_base_device(self, nic0, vlan999):
        assert vlan.is_base_device(nic0)

    def test_identify_non_vlan_base_device(self, nic0):
        assert not vlan.is_base_device(nic0)
