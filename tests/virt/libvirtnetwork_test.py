# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

import re
import xml.etree.ElementTree as ET

from vdsm.virt import libvirtnetwork

from nose.plugins.attrib import attr
from testlib import VdsmTestCase as TestCaseBase


NETWORK = 'awesome_net'
LIBVIRT_NETWORK = 'vdsm-' + NETWORK
IFACE = 'dummy'

NET1 = 'net1'
NET2 = 'net2'


class LibvirtTestCase(TestCaseBase):
    def assertEqualXml(self, a, b):
        """Compare two xml strings for equality"""

        a_xml = ET.tostring(ET.fromstring(a))
        b_xml = ET.tostring(ET.fromstring(b))

        a_xml_normalized = re.sub(br'>\s*\n\s*<', b'><', a_xml).strip()
        b_xml_normalized = re.sub(br'>\s*\n\s*<', b'><', b_xml).strip()

        assert a_xml_normalized == b_xml_normalized


@attr(type='unit')
class LibvirtTests(LibvirtTestCase):

    def test_create_net_xml_with_bridge(self):
        expected_doc = """<network>
                            <name>{}</name>
                            <forward mode='bridge'/>
                            <bridge name='{}'/>
                         </network>""".format(LIBVIRT_NETWORK, NETWORK)
        actual_doc = libvirtnetwork.createNetworkDef(NETWORK, bridged=True)
        self.assertEqualXml(expected_doc, actual_doc)

    def test_create_net_xml_with_iface(self):
        expected_doc = """<network>
                            <name>{}</name>
                            <forward mode='passthrough'>
                              <interface dev='{}'/>
                            </forward>
                          </network>""".format(LIBVIRT_NETWORK, IFACE)
        actual_doc = libvirtnetwork.createNetworkDef(
            NETWORK, bridged=False, iface=IFACE)
        self.assertEqualXml(expected_doc, actual_doc)


@attr(type='unit')
class LibvirtNetworksUsersCacheTests(TestCaseBase):

    def test_add_remove_new_net(self):
        user_ref = self
        assert not libvirtnetwork.NetworksUsersCache.has_users(NET1)

        libvirtnetwork.NetworksUsersCache.add(NET1, user_ref)
        assert libvirtnetwork.NetworksUsersCache.has_users(NET1)

        libvirtnetwork.NetworksUsersCache.remove(NET1, user_ref)
        assert not libvirtnetwork.NetworksUsersCache.has_users(NET1)

    def test_add_remove_existing_net_with_same_user(self):
        user_ref = self
        libvirtnetwork.NetworksUsersCache.add(NET1, user_ref)

        libvirtnetwork.NetworksUsersCache.add(NET1, user_ref)
        assert libvirtnetwork.NetworksUsersCache.has_users(NET1)

        libvirtnetwork.NetworksUsersCache.remove(NET1, user_ref)
        assert not libvirtnetwork.NetworksUsersCache.has_users(NET1)

    def test_add_remove_existing_net_with_unique_users(self):
        user_ref1 = self
        user_ref2 = 12345
        libvirtnetwork.NetworksUsersCache.add(NET1, user_ref1)

        libvirtnetwork.NetworksUsersCache.add(NET1, user_ref2)
        assert libvirtnetwork.NetworksUsersCache.has_users(NET1)

        libvirtnetwork.NetworksUsersCache.remove(NET1, user_ref2)
        assert libvirtnetwork.NetworksUsersCache.has_users(NET1)

        # test teardown
        libvirtnetwork.NetworksUsersCache.remove(NET1, user_ref1)
        assert not libvirtnetwork.NetworksUsersCache.has_users(NET1)

    def test_remove_non_existing_net(self):
        user_ref = self
        assert not libvirtnetwork.NetworksUsersCache.has_users(NET1)

        libvirtnetwork.NetworksUsersCache.remove(NET1, user_ref)

        assert not libvirtnetwork.NetworksUsersCache.has_users(NET1)
