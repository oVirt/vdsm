# Copyright 2016-2020 Red Hat, Inc.
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

from network.nettestlib import dummy_device

from vdsm.network.link.iface import iface


@pytest.fixture
def link_iface():
    with dummy_device() as nic:
        _iface = iface(nic)
        yield _iface


class TestLinkIface(object):
    def test_iface_up(self, link_iface):
        link_iface.up()
        assert link_iface.is_up()

    def test_iface_down(self, link_iface):
        link_iface.up()
        link_iface.down()
        assert not link_iface.is_up()

    def test_iface_notpromisc(self, link_iface):
        link_iface.up()
        assert not link_iface.is_promisc()

    def test_iface_hwaddr(self, link_iface):
        MAC_ADDR = '02:00:00:00:00:01'

        link_iface.set_address(MAC_ADDR)
        assert link_iface.address() == MAC_ADDR
