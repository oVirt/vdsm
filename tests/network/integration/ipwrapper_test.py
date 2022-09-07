# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

import pytest

from network.nettestlib import bond_device
from network.nettestlib import bridge_device
from network.nettestlib import dummy_devices
from network.nettestlib import vlan_device

from vdsm.network import ethtool
from vdsm.network import ipwrapper
from vdsm.network.netlink import monitor
from vdsm.network.netlink.libnl import IfaceStatus


@pytest.fixture
def bridge0():
    with bridge_device() as br:
        yield br


@pytest.fixture
def vlan0(bond0):
    with vlan_device(bond0, 27) as vlan:
        yield vlan


@pytest.fixture
def bond0(nics):
    with bond_device(nics) as bond:
        yield bond


@pytest.fixture
def nics():
    with dummy_devices(2) as nics:
        yield nics


class TestLinks(object):
    def test_get_link(self, bridge0):
        link = ipwrapper.getLink(bridge0)
        assert link.isBRIDGE()
        assert link.oper_up
        assert link.master is None
        assert link.name == bridge0

    def test_missing_bridge_removal_fails(self):
        with pytest.raises(ipwrapper.IPRoute2NoDeviceError):
            ipwrapper.linkDel('missing_bridge')

    def test_ip_link_wrapper(self, bridge0, nics, bond0, vlan0):
        device_links = ipwrapper.getLinks()
        devices = {device.name: device for device in device_links}

        # Test all devices to be there.
        assert set([bridge0, bond0, vlan0] + nics) <= set(devices)

        assert devices[bridge0].isBRIDGE()
        assert devices[nics[0]].isDUMMY()
        assert devices[nics[1]].isDUMMY()
        assert devices[bond0].isBOND()
        assert devices[vlan0].isVLAN()


class TestDrvinfo(object):
    def test_bridge_ethtool_drvinfo(self, bridge0):
        assert ethtool.driver_name(bridge0) == ipwrapper.LinkType.BRIDGE

    def test_enable_promisc(self, bridge0):
        link = ipwrapper.getLink(bridge0)
        with monitor.object_monitor(timeout=2, silent_timeout=True) as mon:
            link.promisc = True
            for event in mon:
                if (
                    event['event'] == 'new_link'
                    and event.get('flags', 0) & IfaceStatus.IFF_PROMISC
                ):
                    return
        assert False, 'Could not enable promiscuous mode.'

    def test_disable_promisc(self, bridge0):
        ipwrapper.getLink(bridge0).promisc = True
        ipwrapper.getLink(bridge0).promisc = False
        assert not ipwrapper.getLink(
            bridge0
        ).promisc, 'Could not disable promiscuous mode.'
