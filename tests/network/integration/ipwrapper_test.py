# -*- coding: utf-8 -*-
# Copyright 2013-2020 Red Hat, Inc.
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

from __future__ import absolute_import
from __future__ import division

import pytest

from network.nettestlib import bond_device
from network.nettestlib import dummy_devices
from network.nettestlib import vlan_device

from vdsm.network import ethtool
from vdsm.network import ipwrapper
from vdsm.network import py2to3
from vdsm.network.netlink import monitor
from vdsm.network.netlink.libnl import IfaceStatus

from .netintegtestlib import Bridge
from .netintegtestlib import bridge_device


@pytest.fixture
def bridge0():
    with bridge_device() as br:
        yield br


@pytest.fixture
def vlan0(bond0):
    with vlan_device(bond0.master, 27) as vlan:
        yield vlan


@pytest.fixture
def bond0(nics):
    with bond_device(nics) as bond:
        bond.up()
        yield bond


@pytest.fixture
def nics():
    with dummy_devices(2) as nics:
        yield nics


class TestLinks(object):
    def testGetLink(self, bridge0):
        link = ipwrapper.getLink(bridge0.devName)
        assert link.isBRIDGE()
        assert link.oper_up
        assert link.master is None
        assert link.name == bridge0.devName

    def test_missing_bridge_removal_fails(self):
        with pytest.raises(ipwrapper.IPRoute2NoDeviceError):
            ipwrapper.linkDel('missing_bridge')

    def test_ip_link_wrapper(self, bridge0, nics, bond0, vlan0):
        device_links = ipwrapper.getLinks()
        devices = {device.name: device for device in device_links}

        # Test all devices to be there.
        assert set(
            [bridge0.devName, bond0.master, vlan0.devName] + nics
        ) <= set(devices)

        assert devices[bridge0.devName].isBRIDGE()
        assert devices[nics[0]].isDUMMY()
        assert devices[nics[1]].isDUMMY()
        assert devices[bond0.master].isBOND()
        assert devices[vlan0.devName].isVLAN()


class TestDrvinfo(object):
    def testBridgeEthtoolDrvinfo(self, bridge0):
        bridge_name = bridge0.devName
        assert ethtool.driver_name(bridge_name) == ipwrapper.LinkType.BRIDGE

    def testEnablePromisc(self, bridge0):
        link = ipwrapper.getLink(bridge0.devName)
        with monitor.object_monitor(timeout=2, silent_timeout=True) as mon:
            link.promisc = True
            for event in mon:
                if (
                    event['event'] == 'new_link'
                    and event.get('flags', 0) & IfaceStatus.IFF_PROMISC
                ):
                    return
        self.fail("Could not enable promiscuous mode.")

    def testDisablePromisc(self, bridge0):
        ipwrapper.getLink(bridge0.devName).promisc = True
        ipwrapper.getLink(bridge0.devName).promisc = False
        assert not ipwrapper.getLink(
            bridge0.devName
        ).promisc, "Could not disable promiscuous mode."


class TestUnicodeDrvinfo(object):
    @pytest.fixture
    def unicode_bridge(self):
        # First 3 Hebrew letters, in native string format
        # See http://unicode.org/charts/PDF/U0590.pdf
        bridge_name = py2to3.to_str(b'\xd7\x90\xd7\x91\xd7\x92')
        br = Bridge(bridge_name)
        br.addDevice()
        try:
            yield br
        finally:
            br.delDevice()

    def testUtf8BridgeEthtoolDrvinfo(self, unicode_bridge):
        driver_name = ethtool.driver_name(unicode_bridge.devName)
        assert driver_name == ipwrapper.LinkType.BRIDGE
