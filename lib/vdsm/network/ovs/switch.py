# Copyright 2016 Red Hat, Inc.
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

import itertools
import random

import six

from vdsm.network import errors as ne
from vdsm.network.netlink import link
from vdsm.network.netinfo import bonding
from vdsm.network.netinfo.nics import nics
from vdsm.utils import random_iface_name

from . import driver
from . import info
from . import validator

SWITCH_TYPE = 'ovs'
BRIDGE_PREFIX = 'vdsmbr_'


def validate_network_setup(nets, bonds):
    _netinfo = info.create_netinfo(info.OvsInfo())
    kernel_nics = nics()
    for net, attrs in six.iteritems(nets):
        validator.validate_net_configuration(
            net, attrs, bonds, _netinfo['bondings'], kernel_nics)
    for bond, attrs in six.iteritems(bonds):
        validator.validate_bond_configuration(
            bond, attrs, nets, _netinfo['networks'], kernel_nics)


def cleanup():
    ovsdb = driver.create()
    with ovsdb.transaction() as t:
        t.add(*_cleanup_unused_bridges(ovsdb))


def create_setup(ovs_info):
    ovsdb = driver.create()
    return Setup(ovsdb, ovs_info)


# TODO: We could move all setup() code into __init__ and __exit__.
class Setup(object):
    def __init__(self, ovsdb, ovs_info):
        self._ovsdb = ovsdb
        self._transaction = self._ovsdb.transaction()
        self._ovs_info = ovs_info
        self._bridges_by_sb = ovs_info.bridges_by_sb
        self._northbounds_by_sb = ovs_info.northbounds_by_sb

        self._acquired_ifaces = set()

    @property
    def acquired_ifaces(self):
        """
        Report the interfaces that have been added to networks/bondings, either
        by add or edit actions, including ifaces that have been removed and
        re-added to a different network/bonding.
        """
        return self._acquired_ifaces

    def __enter__(self):
        return self

    def __exit__(self, type, value, traceback):
        if type is None:
            self._transaction.commit()
        else:
            six.reraise(type, value, traceback)

    def remove_bonds(self, bonds):
        self._transaction.add(
            *[self._ovsdb.del_port(bond) for bond in bonds])

    def edit_bonds(self, bonds):
        detach_commands = []
        attach_commands = []

        for bond, attrs in six.iteritems(bonds):
            bridge = self._bridges_by_sb[bond]

            to_be_configured_slaves = attrs['nics']
            self._acquired_ifaces.update(to_be_configured_slaves)
            running_bond = self._ovs_info.bridges[bridge]['ports'][bond]
            running_slaves = running_bond['bond']['slaves']

            detach, attach = self._edit_slaves(
                bond, running_slaves, to_be_configured_slaves)
            detach_commands.extend(detach)
            attach_commands.extend(attach)

            attach_commands.extend(
                self._edit_mode(bond, attrs.get('options', '')))

        self._transaction.add(*detach_commands)
        self._transaction.add(*attach_commands)

    def add_bonds(self, bonds):
        """
        On a bond creation, OVS bridge is created. Northbound port (network)
        then can be attached to it.
        """
        for bond, attrs in six.iteritems(bonds):
            bridge = self._create_bridge()
            self._bridges_by_sb[bond] = bridge
            to_be_configured_slaves = attrs['nics']
            self._create_sb_bond(bridge, bond, to_be_configured_slaves)
            self._acquired_ifaces.update(to_be_configured_slaves)

            self._transaction.add(*self._edit_mode(
                bond, attrs.get('options', '')))

    def _edit_slaves(self, bond, running_slaves, to_be_configured_slaves):
        running = frozenset(running_slaves)
        to_be_configured = frozenset(to_be_configured_slaves)

        attach = list(itertools.chain.from_iterable(
            self._ovsdb.attach_bond_slave(bond, slave)
            for slave in to_be_configured - running))

        detach = list(itertools.chain.from_iterable(
            self._ovsdb.detach_bond_slave(bond, slave)
            for slave in running - to_be_configured))

        return detach, attach

    def _edit_mode(self, bond, options):
        # TODO: Support setup of all OVS bond-related attributes and move
        # fallback setup into a separate function.

        commands = []

        parsed_options = bonding.parse_bond_options(options)
        mode = parsed_options.get('mode')
        miimon = parsed_options.get('miimon')

        bond_mode = 'active-backup'
        if mode is None:
            lacp = 'off'
        else:
            if mode in ('1', bonding.BONDING_MODES_NUMBER_TO_NAME['1']):
                lacp = 'off'
            elif mode in ('4', bonding.BONDING_MODES_NUMBER_TO_NAME['4']):
                lacp = 'active'
            else:
                # TODO: Validation should be moved to validator.py as soon as
                # we implement bond options canonicalization.
                raise ne.ConfigNetworkError(
                    ne.ERR_BAD_PARAMS,
                    'Mode {} is not available for OVS bondings'.format(mode))
        commands.append(self._ovsdb.set_port_attr(
            bond, 'bond_mode', bond_mode))
        commands.append(self._ovsdb.set_port_attr(bond, 'lacp', lacp))

        if miimon is None:
            bond_detect_mode = 'carrier'
            bond_miimon_interval = None
        else:
            bond_detect_mode = 'miimon'
            bond_miimon_interval = miimon
        commands.append(self._ovsdb.set_port_attr(
            bond, 'other_config:bond-detect-mode', bond_detect_mode))
        if bond_miimon_interval is not None:
            commands.append(self._ovsdb.set_port_attr(
                bond, 'other_config:bond-miimon-interval',
                bond_miimon_interval))

        return commands

    def remove_nets(self, nets):
        ovs_netinfo = info.create_netinfo(self._ovs_info)
        running_networks = ovs_netinfo['networks']
        for net in nets:
            running_attrs = running_networks[net]
            bond = running_attrs['bond']
            nic = running_attrs['nics'][0] if not bond else None
            sb = nic or bond

            self._northbounds_by_sb[sb].discard(net)

            # Detach NIC if not used anymore.
            if nic and not self._northbounds_by_sb[nic]:
                self._detach_sb_nic(nic)

            self._transaction.add(self._ovsdb.del_port(net))

    def _detach_sb_nic(self, nic):
        self._northbounds_by_sb.pop(nic)
        self._bridges_by_sb.pop(nic)
        self._transaction.add(self._ovsdb.del_port(nic))

    def add_nets(self, nets):
        for net, attrs in six.iteritems(nets):
            nic = attrs.get('nic')
            if nic:
                self._acquired_ifaces.add(nic)
            bond = attrs.get('bonding')
            sb = nic or bond
            if sb in self._bridges_by_sb:
                bridge = self._bridges_by_sb[sb]
            else:
                bridge = self._create_bridge()
                self._bridges_by_sb[nic] = bridge
                self._create_sb_nic(bridge, nic)

            self._create_nb(bridge, net)
            vlan = attrs.get('vlan')
            if vlan is not None:
                self._set_vlan(net, vlan)
            if nic is not None and vlan is None:
                self._copy_nic_hwaddr_to_nb(net, nic)

            self._northbounds_by_sb.setdefault(sb, set()).add(net)

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

    def _create_sb_bond(self, bridge, bond, slaves):
        self._transaction.add(self._ovsdb.add_bond(
            bridge, bond, slaves, fake_iface=True))
        self._transaction.add(self._ovsdb.set_port_attr(
            bond, 'other_config:vdsm_level', info.SOUTHBOUND))


def _random_unicast_local_mac():
    macaddr = random.randint(0x000000000000, 0xffffffffffff)
    macaddr |= 0x020000000000  # locally administered
    macaddr &= 0xfeffffffffff  # unicast
    macaddr_str = '{:0>12x}'.format(macaddr)
    return ':'.join([macaddr_str[i:i+2]
                     for i in range(0, len(macaddr_str), 2)])


def _get_mac(iface):
    return link.get_link(iface)['address']


def _cleanup_unused_bridges(ovsdb):
    """
    Remove bridges with no ports. Southbound ports are detached from bridge by
    Setup.remove_bonds() and Setup.detach_unused_sb_nics(). Northbound ports
    are detached by Setup.remove_nets().
    """
    return [ovsdb.del_br(bridge) for bridge in _unused_bridges()]


def _unused_bridges():
    unused_bridges = set()
    ovs_info = info.OvsInfo()
    for bridge, attrs in six.iteritems(ovs_info.bridges):
        ports = attrs['ports']
        northbound_ports = ovs_info.northbound_ports(ports)
        southbound_port = ovs_info.southbound_port(ports)
        if (bridge.startswith(BRIDGE_PREFIX) and not list(northbound_ports) and
                not southbound_port):
            unused_bridges.add(bridge)
    return unused_bridges
