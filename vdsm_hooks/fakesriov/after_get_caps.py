#!/usr/bin/python
#
# Copyright 2015 Red Hat, Inc.
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
"""
To Enable this set fake_sriov_enable=true in /etc/vdsm/vdsm.conf.
"""
import hooking
from vdsm.config import config

_PASSTHROUGH_SUPPORT = {'hostdevPassthrough': True}
_PF_DEVICE_NAME = 'enp22s0f0'
_VF0_DEVICE_NAME = 'enp22s16'
_VF1_DEVICE_NAME = 'enp22s16f2'
_NICS = {
    _PF_DEVICE_NAME: {
        'addr': '', 'cfg': {'BOOTPROTO': 'none', 'BRIDGE': 'ovirtmgmt',
                            'DEVICE': _PF_DEVICE_NAME,
                            'HWADDR': '78:e7:d1:e4:8f:16', 'IPV6INIT': 'no',
                            'MTU': '1500', 'NM_CONTROLLED': 'no',
                            'ONBOOT': 'yes'}, 'dhcpv4': False, 'dhcpv6': False,
        'gateway': '', 'hwaddr': '78:e7:d1:e4:8f:16', 'ipv4addrs': [],
        'ipv6addrs': ['fe80::7ae7:d1ff:fee4:8f16/64'], 'ipv6gateway': '::',
        'mtu': '1500', 'netmask': '', 'speed': 1000},
    _VF0_DEVICE_NAME: {'addr': '', 'cfg': {'BOOTPROTO': 'none',
                                           'DEVICE': _VF0_DEVICE_NAME,
                                           'HWADDR': 'be:b8:2b:a8:15:bf',
                                           'MTU': '1500',
                                           'NM_CONTROLLED': 'no',
                                           'ONBOOT': 'yes'},
                       'dhcpv4': False, 'dhcpv6': False, 'gateway': '',
                       'hwaddr': 'aa:8c:31:98:8a:9a', 'ipv4addrs': [],
                       'ipv6addrs': [], 'ipv6gateway': '::', 'mtu': '1500',
                       'netmask': '', 'speed': 0},
    _VF1_DEVICE_NAME: {'addr': '', 'cfg': {'BOOTPROTO': 'none',
                                           'BRIDGE': 'net',
                                           'DEVICE': _VF1_DEVICE_NAME,
                                           'HWADDR': '26:83:76:ec:08:6b',
                                           'IPV6INIT': 'no', 'MTU': '1500',
                                           'NM_CONTROLLED': 'no',
                                           'ONBOOT': 'yes'},
                       'dhcpv4': False, 'dhcpv6': False, 'gateway': '',
                       'hwaddr': 'ce:1f:0b:d1:ca:93', 'ipv4addrs': [],
                       'ipv6addrs': [], 'ipv6gateway': '::', 'mtu': '1500',
                       'netmask': '', 'speed': 0}}

if __name__ == '__main__':
    if config.getboolean('vars', 'fake_sriov_enable'):
        caps = hooking.read_json()
        caps.update(_PASSTHROUGH_SUPPORT)
        caps['nics'].update(_NICS)
        hooking.write_json(caps)
