#
# Copyright 2015 Hat, Inc.
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
from __future__ import absolute_import


def set_netdev_dhcp_info(network_info, networking):
    dev_info = _device_lookup(network_info['iface'], networking)
    if dev_info:
        dev_info['dhcpv4'] = network_info['dhcpv4']
        dev_info['dhcpv6'] = network_info['dhcpv6']


def _device_lookup(netdev, networking):
    for dev_type in ('bridges', 'vlans', 'bondings', 'nics'):
        dev_info = networking[dev_type].get(netdev)
        if dev_info:
            return dev_info
    return None
