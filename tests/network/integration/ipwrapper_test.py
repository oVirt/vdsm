# -*- coding: utf-8 -*-
# Copyright 2013-2019 Red Hat, Inc.
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
import six

from vdsm.network import ethtool
from vdsm.network import ipwrapper
from vdsm.network import py2to3
from vdsm.network.netlink import monitor
from vdsm.network.netlink.libnl import IfaceStatus

from ..nettestlib import Bridge, bridge_device
from testlib import VdsmTestCase as TestCaseBase


class TestLinks(TestCaseBase):
    def testGetLink(self):
        with bridge_device() as bridge:
            link = ipwrapper.getLink(bridge.devName)
            self.assertTrue(link.isBRIDGE)
            self.assertTrue(link.oper_up)
            self.assertEqual(link.master, None)
            self.assertEqual(link.name, bridge.devName)

    def test_missing_bridge_removal_fails(self):
        with self.assertRaises(ipwrapper.IPRoute2NoDeviceError):
            ipwrapper.linkDel('missing_bridge')


class TestDrvinfo(TestCaseBase):
    def setUp(self):
        self._bridge = Bridge()
        self._bridge.addDevice()

    def tearDown(self):
        self._bridge.delDevice()

    def testBridgeEthtoolDrvinfo(self):
        self.assertEqual(
            ethtool.driver_name(self._bridge.devName),
            ipwrapper.LinkType.BRIDGE,
        )

    def testEnablePromisc(self):
        link = ipwrapper.getLink(self._bridge.devName)
        with monitor.Monitor(timeout=2, silent_timeout=True) as mon:
            link.promisc = True
            for event in mon:
                if (
                    event['event'] == 'new_link'
                    and event.get('flags', 0) & IfaceStatus.IFF_PROMISC
                ):
                    return
        self.fail("Could not enable promiscuous mode.")

    def testDisablePromisc(self):
        ipwrapper.getLink(self._bridge.devName).promisc = True
        ipwrapper.getLink(self._bridge.devName).promisc = False
        self.assertFalse(
            ipwrapper.getLink(self._bridge.devName).promisc,
            "Could not disable promiscuous mode.",
        )


class TestUnicodeDrvinfo(TestCaseBase):
    def setUp(self):
        if six.PY3:
            pytest.skip(
                'Passing non-ascii chars to cmdline is broken in Python 3'
            )

        # First 3 Hebrew letters, in native string format
        # See http://unicode.org/charts/PDF/U0590.pdf
        bridge_name = py2to3.to_str(b'\xd7\x90\xd7\x91\xd7\x92')
        self._bridge = Bridge(bridge_name)
        self._bridge.addDevice()

    def tearDown(self):
        self._bridge.delDevice()

    def testUtf8BridgeEthtoolDrvinfo(self):
        self.assertEqual(
            ethtool.driver_name(self._bridge.devName),
            ipwrapper.LinkType.BRIDGE,
        )
