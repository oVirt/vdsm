# Copyright 2016-2017 Red Hat, Inc.
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
from __future__ import absolute_import

from contextlib import contextmanager
import random

import six

from vdsm.network.link.bond import Bond
from vdsm.network.link.iface import random_iface_name
from vdsm.network.netinfo.nics import nics
from vdsm.network.netlink import link

from . import driver
from . import info
from . import validator

SWITCH_TYPE = 'ovs'
BRIDGE_PREFIX = 'vdsmbr_'


def validate_network_setup(nets, bonds):
    ovs_networks = info.create_netinfo(info.OvsInfo())['networks']
    kernel_nics = nics()
    kernel_bonds = Bond.bonds()
    for net, attrs in six.iteritems(nets):
        validator.validate_net_configuration(
            net, attrs, bonds, kernel_bonds, kernel_nics)
    for bond, attrs in six.iteritems(bonds):
        validator.validate_bond_configuration(
            bond, attrs, nets, ovs_networks, kernel_nics)


def create_network_removal_setup(ovs_info):
    ovsdb = driver.create()
    return NetsRemovalSetup(ovsdb, ovs_info)


def create_network_addition_setup(ovs_info):
    ovsdb = driver.create()
    return NetsAdditionSetup(ovsdb, ovs_info)


class NetsRemovalSetup(object):
    def __init__(self, ovsdb, ovs_info):
        self._ovsdb = ovsdb
        self._ovs_info = ovs_info
        self._transaction = ovsdb.transaction()

    def remove(self, nets):
        ovs_netinfo = info.create_netinfo(self._ovs_info)
        running_networks = ovs_netinfo['networks']
        with self._transaction:
            for net in nets:
                sb = self._get_southbound(net, running_networks)
                self._remove_northbound(net, sb)
                self._detach_unused_southbound(sb)

    def _remove_northbound(self, net, sb):
        bridge = self._ovs_info.bridges_by_sb[sb]
        self._ovs_info.bridges[bridge]['ports'].pop(net)
        self._ovs_info.northbounds_by_sb[sb].discard(net)
        self._transaction.add(self._ovsdb.del_port(net))

    def _detach_unused_southbound(self, sb):
        if sb and not self._ovs_info.northbounds_by_sb[sb]:
            self._ovs_info.northbounds_by_sb.pop(sb)
            bridge_without_sb = self._ovs_info.bridges_by_sb.pop(sb)
            self._ovs_info.bridges.pop(bridge_without_sb)

            self._transaction.add(self._ovsdb.del_port(sb))
            self._transaction.add(self._ovsdb.del_br(bridge_without_sb))

    @staticmethod
    def _get_southbound(net, running_networks):
        running_attrs = running_networks[net]
        bond = running_attrs['bond']
        nic = running_attrs['nics'][0] if not bond else None
        return nic or bond


class NetsAdditionSetup(object):
    def __init__(self, ovsdb, ovs_info):
        self._ovsdb = ovsdb
        self._ovs_info = ovs_info
        self._transaction = ovsdb.transaction()
        self._acquired_ifaces = set()

    @contextmanager
    def add(self, nets):
        with self._transaction:
            for net, attrs in six.iteritems(nets):
                nic = attrs.get('nic')
                bond = attrs.get('bonding')
                sb = nic or bond
                self._acquired_ifaces.add(sb)

                bridge = self._get_ovs_bridge(sb)
                self._create_nb(bridge, net)

                vlan = attrs.get('vlan')
                if vlan is not None:
                    self._set_vlan(net, vlan)

                # FIXME: What about an existing bond?
                if nic is not None and vlan is None:
                    self._copy_nic_hwaddr_to_nb(net, nic)

                self._ovs_info.northbounds_by_sb.setdefault(sb, set())
                self._ovs_info.northbounds_by_sb[sb].add(net)
            yield

    @property
    def acquired_ifaces(self):
        """
        Report the interfaces that have been added to networks, either
        by add or edit actions, including ifaces that have been removed and
        re-added to a different network.
        """
        return self._acquired_ifaces

    def _get_ovs_bridge(self, sb):
        if sb in self._ovs_info.bridges_by_sb:
            bridge = self._ovs_info.bridges_by_sb[sb]
        else:
            bridge = self._create_bridge()
            self._ovs_info.bridges_by_sb[sb] = bridge
            self._create_sb_nic(bridge, sb)
        return bridge

    def _create_nb(self, bridge, port):
        self._transaction.add(self._ovsdb.add_port(bridge, port))
        self._transaction.add(self._ovsdb.set_port_attr(
            port, 'other_config:vdsm_level', info.NORTHBOUND))
        self._transaction.add(self._ovsdb.set_interface_attr(
            port, 'type', 'internal'))

    def _set_vlan(self, net, vlan):
        self._transaction.add(self._ovsdb.set_port_attr(net, 'tag', vlan))

    def _copy_nic_hwaddr_to_nb(self, net, nic):
        nic_mac = _get_mac(nic)
        self._transaction.add(self._ovsdb.set_interface_attr(
            net, 'mac', nic_mac))

    def _create_bridge(self):
        bridge = self._create_br_name()
        self._transaction.add(self._ovsdb.add_br(bridge))
        self._transaction.add(self._ovsdb.set_bridge_attr(
            bridge, 'other-config:hwaddr', _random_unicast_local_mac()))
        return bridge

    @staticmethod
    def _create_br_name():
        return random_iface_name(prefix=BRIDGE_PREFIX)

    def _create_sb_nic(self, bridge, nic):
        self._transaction.add(self._ovsdb.add_port(bridge, nic))
        self._transaction.add(self._ovsdb.set_port_attr(
            nic, 'other_config:vdsm_level', info.SOUTHBOUND))


def _random_unicast_local_mac():
    macaddr = random.randint(0x000000000000, 0xffffffffffff)
    macaddr |= 0x020000000000  # locally administered
    macaddr &= 0xfeffffffffff  # unicast
    macaddr_str = '{:0>12x}'.format(macaddr)
    return ':'.join([macaddr_str[i:i + 2]
                     for i in range(0, len(macaddr_str), 2)])


def _get_mac(iface):
    return link.get_link(iface)['address']
