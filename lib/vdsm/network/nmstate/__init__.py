# Copyright 2020-2021 Red Hat, Inc.
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
#

# Re-export public API
from .api import generate_state
from .api import get_interfaces
from .api import get_nameservers
from .api import get_routes
from .api import is_autoconf_enabled
from .api import is_dhcp_enabled
from .api import ovs_netinfo
from .api import prepare_ovs_bridge_mappings
from .api import setup
from .api import state_show
from .api import update_num_vfs

# Re-export nmstate schema
from .schema import BondSchema
from .schema import DNS
from .schema import Ethernet
from .schema import Interface
from .schema import InterfaceIP
from .schema import InterfaceIPv6
from .schema import InterfaceState
from .schema import InterfaceType
from .schema import LinuxBridge
from .schema import OvsBridgeSchema
from .schema import Route
from .schema import Vlan


__all__ = [
    'generate_state',
    'get_interfaces',
    'get_nameservers',
    'get_routes',
    'is_autoconf_enabled',
    'is_dhcp_enabled',
    'ovs_netinfo',
    'prepare_ovs_bridge_mappings',
    'setup',
    'state_show',
    'update_num_vfs',
    'BondSchema',
    'DNS',
    'Ethernet',
    'Interface',
    'InterfaceIP',
    'InterfaceIPv6',
    'InterfaceState',
    'InterfaceType',
    'LinuxBridge',
    'OvsBridgeSchema',
    'Route',
    'Vlan',
]
