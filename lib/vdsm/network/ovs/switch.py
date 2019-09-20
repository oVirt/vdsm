# Copyright 2016-2018 Red Hat, Inc.
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
from __future__ import division

import random

import six

from vdsm.network.link import dpdk
from vdsm.network.link.iface import DEFAULT_MTU
from vdsm.network.link.iface import random_iface_name
from vdsm.network.netlink import link

from . import driver
from . import info

SWITCH_TYPE = 'ovs'
BRIDGE_PREFIX = 'vdsmbr_'


ovsdb = driver.create()


def add_vhostuser_port(bridge, port, socket_path):
    with ovsdb.transaction() as t:
        t.add(ovsdb.add_port(bridge, port))
        t.add(ovsdb.set_vhostuser_iface(port, socket_path))


def remove_port(bridge, port):
    with ovsdb.transaction() as t:
        t.add(ovsdb.del_port(port, bridge))


class NetsRemovalSetup(object):
    def __init__(self, ovs_info):
        self._ovs_info = ovs_info
        self._transaction = ovsdb.transaction()

    def prepare_setup(self, nets):
        """Prepare setup for networks removal"""
        ovs_netinfo = info.create_netinfo(self._ovs_info)
        running_networks = ovs_netinfo['networks']
        for net in nets:
            sb = running_networks[net]['southbound']
            self._remove_northbound(net, sb)
            self._detach_unused_southbound(sb)

        self._set_network_mtu()

    def commit_setup(self):
        self._transaction.commit()

    def _remove_northbound(self, net, sb):
        bridge = self._ovs_info.bridges_by_sb[sb]
        self._ovs_info.bridges[bridge]['ports'].pop(net)
        self._ovs_info.northbounds_by_sb[sb].discard(net)
        self._transaction.add(ovsdb.del_port(net))

    def _detach_unused_southbound(self, sb):
        if sb and not self._ovs_info.northbounds_by_sb[sb]:
            self._ovs_info.northbounds_by_sb.pop(sb)
            bridge_without_sb = self._ovs_info.bridges_by_sb.pop(sb)
            self._ovs_info.bridges.pop(bridge_without_sb)

            self._transaction.add(ovsdb.del_port(sb))
            self._transaction.add(ovsdb.del_br(bridge_without_sb))
            ovsdb.set_interface_attr(sb, 'mtu_request', DEFAULT_MTU).execute()

    def _set_network_mtu(self):
        for sb, nbs in six.viewitems(self._ovs_info.northbounds_by_sb):
            if dpdk.is_dpdk(sb):
                continue
            max_nb_mtu = max(_get_mtu(nb) for nb in nbs)
            sb_mtu = _get_mtu(sb)
            if max_nb_mtu and sb_mtu != max_nb_mtu:
                self._set_mtu(sb, max_nb_mtu)

    def _set_mtu(self, port, mtu):
        self._transaction.add(
            ovsdb.set_interface_attr(port, 'mtu_request', mtu)
        )


class NetsAdditionSetup(object):
    def __init__(self, ovs_info):
        self._ovs_info = ovs_info
        self._transaction = ovsdb.transaction()
        self._acquired_ifaces = set()

    def prepare_setup(self, nets):
        """Prepare networks for creation"""
        sb_max_mtu_map = {}
        for net, attrs in six.iteritems(nets):
            nic = attrs.get('nic')
            bond = attrs.get('bonding')
            sb = nic or bond
            if not dpdk.is_dpdk(sb):
                self._acquired_ifaces.add(sb)

            sb_exists = sb in self._ovs_info.bridges_by_sb

            bridge = self._get_ovs_bridge(sb, sb_exists)
            self._create_nb(bridge, net)

            vlan = attrs.get('vlan')
            if vlan is not None:
                self._set_vlan(net, vlan)

            self._prepare_network_sb_mtu(
                sb, sb_exists, attrs['mtu'], sb_max_mtu_map
            )

            # FIXME: What about an existing bond?
            if nic is not None and vlan is None:
                self._copy_nic_hwaddr_to_nb(net, nic)

            self._ovs_info.northbounds_by_sb.setdefault(sb, set())
            self._ovs_info.northbounds_by_sb[sb].add(net)

        self._set_networks_mtu(nets, sb_max_mtu_map)

    def commit_setup(self):
        self._transaction.commit()

    @property
    def acquired_ifaces(self):
        """
        Report the interfaces that have been added to networks, either
        by add or edit actions, including ifaces that have been removed and
        re-added to a different network.
        """
        return self._acquired_ifaces

    def _get_ovs_bridge(self, sb, sb_exists):
        if sb_exists:
            bridge = self._ovs_info.bridges_by_sb[sb]
        else:
            dpdk_enabled = dpdk.is_dpdk(sb)
            bridge = self._create_bridge(dpdk_enabled)
            self._ovs_info.bridges_by_sb[sb] = bridge
            self._create_sb_nic(bridge, sb, dpdk_enabled)
        return bridge

    def _create_nb(self, bridge, port):
        self._transaction.add(ovsdb.add_port(bridge, port))
        self._transaction.add(
            ovsdb.set_port_attr(
                port, 'other_config:vdsm_level', info.NORTHBOUND
            )
        )
        self._transaction.add(
            ovsdb.set_interface_attr(port, 'type', 'internal')
        )

    @staticmethod
    def _prepare_network_sb_mtu(sb, sb_exists, desired_mtu, sb_max_mtu_map):
        if sb not in sb_max_mtu_map or sb_max_mtu_map[sb] < desired_mtu:
            sb_max_mtu_map[sb] = desired_mtu

    def _set_networks_mtu(self, nets, sb_max_mtu_map):
        for net, netattrs in six.viewitems(nets):
            self._set_mtu(net, netattrs['mtu'])

        for sb, mtu in six.viewitems(sb_max_mtu_map):
            if mtu:
                self._set_mtu(sb, mtu)

    def _set_mtu(self, port, mtu):
        self._transaction.add(
            ovsdb.set_interface_attr(port, 'mtu_request', mtu)
        )

    def _set_vlan(self, net, vlan):
        self._transaction.add(ovsdb.set_port_attr(net, 'tag', vlan))

    def _copy_nic_hwaddr_to_nb(self, net, nic):
        nic_mac = _get_mac(nic)
        self._transaction.add(ovsdb.set_interface_attr(net, 'mac', nic_mac))

    def _create_bridge(self, dpdk_enabled=False):
        bridge = self._create_br_name()
        self._transaction.add(ovsdb.add_br(bridge))
        if dpdk_enabled:
            self._transaction.add(ovsdb.set_dpdk_bridge(bridge))
        self._transaction.add(
            ovsdb.set_bridge_attr(
                bridge, 'other-config:hwaddr', _random_unicast_local_mac()
            )
        )
        return bridge

    @staticmethod
    def _create_br_name():
        return random_iface_name(prefix=BRIDGE_PREFIX)

    def _create_sb_nic(self, bridge, nic, dpdk_enabled=False):
        self._transaction.add(ovsdb.add_port(bridge, nic))
        if dpdk_enabled:
            pci_addr = dpdk.pci_addr(nic)
            self._transaction.add(ovsdb.set_dpdk_port(nic, pci_addr))
        self._transaction.add(
            ovsdb.set_port_attr(
                nic, 'other_config:vdsm_level', info.SOUTHBOUND
            )
        )


def update_network_to_bridge_mappings(ovs_info):
    net2bridge_mappings_pairs = []
    for bridge, networks in six.viewitems(ovs_info.northbounds_by_bridges):
        for network in networks:
            net2bridge_mappings_pairs.append('{}:{}'.format(network, bridge))
    net2bridge_mappings = ','.join(net2bridge_mappings_pairs) or '""'
    ovsdb.set_db_entry(
        'open', '.', 'external-ids:ovn-bridge-mappings', net2bridge_mappings
    ).execute()


def _random_unicast_local_mac():
    macaddr = random.randint(0x000000000000, 0xFFFFFFFFFFFF)
    macaddr |= 0x020000000000  # locally administered
    macaddr &= 0xFEFFFFFFFFFF  # unicast
    macaddr_str = '{:0>12x}'.format(macaddr)
    return ':'.join(
        [macaddr_str[i : (i + 2)] for i in range(0, len(macaddr_str), 2)]
    )


def _get_mac(iface):
    if dpdk.is_dpdk(iface):
        return dpdk.link_info(iface)['address']
    return link.get_link(iface)['address']


def _get_mtu(port):
    interface_data = ovsdb.list_interface_info(port).execute()
    return interface_data[0]['mtu']
