#
# Copyright 2017 Red Hat, Inc.
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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#

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
