#
# Copyright 2012-2019 Red Hat, Inc.
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

from six import StringIO

from vdsm.network.configurators import ifcfg_acquire

from network.compat import mock
from testlib import VdsmTestCase as TestCaseBase


IFCFG_ETH_CONF = """DEVICE="testdevice"
ONBOOT=yes
NETBOOT=yes
UUID="237dcf6d-516d-4a85-8651-f81e2f4a6238"
IPV6INIT=yes
BOOTPROTO=dhcp
TYPE=Ethernet
NAME="enp0s25"
DEFROUTE=yes
IPV4_FAILURE_FATAL=no
IPV6_AUTOCONF=yes
IPV6_DEFROUTE=yes
IPV6_FAILURE_FATAL=no
HWADDR=68:F7:28:C3:CE:E5
PEERDNS=yes
PEERROUTES=yes
IPV6_PEERDNS=yes
IPV6_PEERROUTES=yes
"""

IFCFG_VLAN_CONF = """VLAN=yes
TYPE=Vlan
PHYSDEV=testdevice
VLAN_ID=100
REORDER_HDR=0
BOOTPROTO=none
IPADDR=19.19.19.19
PREFIX=29
DEFROUTE=yes
IPV4_FAILURE_FATAL=no
IPV6INIT=yes
IPV6_AUTOCONF=yes
IPV6_DEFROUTE=yes
IPV6_PEERDNS=yes
IPV6_PEERROUTES=yes
IPV6_FAILURE_FATAL=no
NAME=vlan
UUID=95d45ecb-99f8-46cd-8942-a34cb5c1e321
ONBOOT=yes

"""


@mock.patch.object(ifcfg_acquire.networkmanager, 'is_running', lambda: False)
@mock.patch.object(ifcfg_acquire.fileutils, 'rm_file')
@mock.patch.object(ifcfg_acquire.os, 'rename')
@mock.patch.object(ifcfg_acquire.glob, 'iglob')
@mock.patch.object(ifcfg_acquire.misc, 'open', create=True)
class TestIfcfgAcquireNMoffline(TestCaseBase):
    def test_acquire_iface_given_non_standard_filename(
        self, mock_open, mock_list_files, mock_rename, mock_rmfile
    ):
        mock_open.return_value.__enter__.side_effect = lambda: StringIO(
            IFCFG_ETH_CONF
        )
        mock_list_files.return_value = ['filename1']

        ifcfg_acquire.IfcfgAcquire.acquire_device('testdevice')

        mock_rename.assert_called_once_with(
            'filename1', ifcfg_acquire.NET_CONF_PREF + 'testdevice'
        )

    def test_acquire_iface_given_multiple_files_for_the_iface(
        self, mock_open, mock_list_files, mock_rename, mock_rmfile
    ):
        mock_open.return_value.__enter__.side_effect = lambda: StringIO(
            IFCFG_ETH_CONF
        )
        mock_list_files.return_value = ['filename1', 'filename2']

        ifcfg_acquire.IfcfgAcquire.acquire_device('testdevice')

        mock_rename.assert_called_once_with(
            'filename1', ifcfg_acquire.NET_CONF_PREF + 'testdevice'
        )
        mock_rmfile.assert_called_once_with('filename2')

    def test_acquire_vlan_iface_given_nm_unique_config(
        self, mock_open, mock_list_files, mock_rename, mock_rmfile
    ):
        mock_open.return_value.__enter__.side_effect = lambda: StringIO(
            IFCFG_VLAN_CONF
        )
        mock_list_files.return_value = ['filename1', 'filename2']

        ifcfg_acquire.IfcfgAcquire.acquire_vlan_device('testdevice.100')

        mock_rename.assert_called_once_with(
            'filename1', ifcfg_acquire.NET_CONF_PREF + 'testdevice.100'
        )
        mock_rmfile.assert_called_once_with('filename2')
