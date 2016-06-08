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

from contextlib import contextmanager
import itertools

import six

from vdsm.network.netconfpersistence import RunningConfig
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


@contextmanager
def transaction(in_rollback, nets, bonds):
    # FIXME: This and _update_running_config are temporary functions handling
    # only positive flows.
    running_config = RunningConfig()
    try:
        yield
    except:
        raise
    finally:
        _update_running_config(nets, bonds, running_config)
        running_config.save()


def _update_running_config(networks, bondings, running_config):
    for net, attrs in six.iteritems(networks):
        if 'remove' in attrs:
            running_config.removeNetwork(net)
        else:
            running_config.setNetwork(net, attrs)

    for bond, attrs in six.iteritems(bondings):
        if 'remove' in attrs:
            running_config.removeBonding(bond)
        else:
            running_config.setBonding(bond, attrs)


def setup(nets, bonds):
    ovs_info = info.OvsInfo()
    _netinfo = info.create_netinfo(ovs_info)
    nets2add, nets2remove = _split_nets_action(nets, _netinfo['networks'])
    bonds2add, bonds2edit, bonds2remove = _split_bonds_action(
        bonds, _netinfo['bondings'])

    _setup_ovs_devices(ovs_info, nets2add, nets2remove, bonds2add, bonds2edit,
                       bonds2remove)


def _split_nets_action(nets, running_nets):
    # TODO: If a nework is to be edited, we remove it and recreate again.
    # We should implement editation.
    nets2remove = set()
    nets2add = {}

    for net, attrs in six.iteritems(nets):
        if 'remove' in attrs:
            nets2remove.add(net)
        elif net in running_nets:
            nets2remove.add(net)
            nets2add[net] = attrs
        else:
            nets2add[net] = attrs

    return nets2add, nets2remove


def _split_bonds_action(bonds, configured_bonds):
    bonds2remove = set()
    bonds2edit = {}
    bonds2add = {}

    for bond, attrs in six.iteritems(bonds):
        if 'remove' in attrs:
            bonds2remove.add(bond)
        elif bond not in configured_bonds:
            bonds2add[bond] = attrs
        elif attrs != configured_bonds.get(bond):
            bonds2edit[bond] = attrs

    return bonds2add, bonds2edit, bonds2remove


def _setup_ovs_devices(ovs_info, nets2add, nets2remove, bonds2add, bonds2edit,
                       bonds2remove):
    ovsdb = driver.create()

    with Setup(ovsdb, ovs_info) as s:
        s.remove_nets(nets2remove)
        s.remove_bonds(bonds2remove)
        s.edit_bonds(bonds2edit)
        s.add_bonds(bonds2add)
        s.add_nets(nets2add)

    with ovsdb.transaction() as t:
        t.add(*_cleanup_unused_bridges(ovsdb))


# TODO: We could move all setup() code into __init__ and __exit__.
class Setup(object):
    def __init__(self, ovsdb, ovs_info):
        self._ovsdb = ovsdb
        self._transaction = self._ovsdb.transaction()
        self._ovs_info = ovs_info
        self._bridges_by_sb = ovs_info.bridges_by_sb
        self._northbounds_by_sb = ovs_info.northbounds_by_sb

    def __enter__(self):
        return self

    def __exit__(self, type, value, traceback):
        if type is None:
            self._transaction.commit()
        else:
            six.reraise(type, value, traceback)

    def remove_bonds(self, bonds):
        self._transaction.add(*[self._ovsdb.del_port(bond) for bond in bonds])

    def edit_bonds(self, bonds):
        detach_commands = []
        attach_commands = []

        for bond, attrs in six.iteritems(bonds):
            bridge = self._bridges_by_sb[bond]

            to_be_configured_slaves = attrs['nics']
            running_bond = self._ovs_info.bridges[bridge]['ports'][bond]
            running_slaves = running_bond['bond']['slaves']

            detach, attach = self._edit_slaves(
                bond, running_slaves, to_be_configured_slaves)
            detach_commands.extend(detach)
            attach_commands.extend(attach)

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
            self._create_sb_bond(bridge, bond, attrs['nics'])

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

            self._northbounds_by_sb.setdefault(sb, set()).add(net)

    def _create_nb(self, bridge, port):
        self._transaction.add(self._ovsdb.add_port(bridge, port))
        self._transaction.add(self._ovsdb.set_port_attr(
            port, 'other_config:vdsm_level', info.NORTHBOUND))
        self._transaction.add(self._ovsdb.set_interface_attr(
            port, 'type', 'internal'))

    def _set_vlan(self, net, vlan):
        self._transaction.add(self._ovsdb.set_port_attr(net, 'tag', vlan))

    def _create_bridge(self):
        bridge = self._create_br_name()
        self._transaction.add(self._ovsdb.add_br(bridge))
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
