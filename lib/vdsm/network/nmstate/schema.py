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

from libnmstate.schema import Bond as BondSchema
from libnmstate.schema import DNS
from libnmstate.schema import Ethernet
from libnmstate.schema import Interface
from libnmstate.schema import InterfaceIP
from libnmstate.schema import InterfaceIPv6
from libnmstate.schema import InterfaceState
from libnmstate.schema import InterfaceType
from libnmstate.schema import LinuxBridge
from libnmstate.schema import OVSBridge as OvsBridgeSchema
from libnmstate.schema import Route
from libnmstate.schema import VLAN as Vlan


__all__ = [
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
