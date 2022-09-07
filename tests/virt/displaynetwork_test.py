# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

from unittest import mock

from vdsm.virt import displaynetwork

from testlib import VdsmTestCase


NETNAME = 'net0'
REFID = '123'


@mock.patch.object(displaynetwork.libvirtnetwork, 'create_network')
class TestDisplayNetwork(VdsmTestCase):

    @mock.patch.object(displaynetwork.net_api, 'network_northbound',
                       lambda net: net)
    @mock.patch.object(displaynetwork.libvirtnetwork, 'delete_network')
    def test_display_net_on_a_vm_net(
            self, libvirt_del_net, libvirt_create_net):
        displaynetwork.create_network(NETNAME, REFID)
        libvirt_create_net.assert_called_with(NETNAME, NETNAME, REFID)

        displaynetwork.delete_network(NETNAME, REFID)
        libvirt_del_net.assert_called_with(NETNAME, REFID)

    @mock.patch.object(displaynetwork.net_api, 'network_northbound',
                       lambda net: 'eth0')
    def test_display_net_on_a_non_vm_net(self, libvirt_create_net):
        displaynetwork.create_network(NETNAME, REFID)
        libvirt_create_net.assert_called_with(NETNAME, 'eth0', REFID)

    @mock.patch.object(displaynetwork.net_api, 'network_northbound',
                       lambda net: None)
    def test_display_net_on_a_missing_net(self, libvirt_create_net):
        displaynetwork.create_network(NETNAME, REFID)
        libvirt_create_net.assert_called_with(NETNAME, None, REFID)
