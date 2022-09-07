# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

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
