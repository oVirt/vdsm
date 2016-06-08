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
    nets_to_be_added, nets_to_be_removed = _split_nets_action(
        nets, _netinfo['networks'])
    bonds_to_be_added_or_edited, bonds_to_be_removed = _split_bonds_action(
        bonds, _netinfo['bondings'])

    _setup_ovs_devices(nets_to_be_added, nets_to_be_removed)


def _split_nets_action(nets, running_nets):
    # TODO: If a nework is to be edited, we remove it and recreate again.
    # We should implement editation.
    nets_to_be_removed = set()
    nets_to_be_added = {}

    for net, attrs in six.iteritems(nets):
        if 'remove' in attrs:
            nets_to_be_removed.add(net)
        elif net in running_nets:
            nets_to_be_removed.add(net)
            nets_to_be_added[net] = attrs
        else:
            nets_to_be_added[net] = attrs

    return nets_to_be_added, nets_to_be_removed


def _split_bonds_action(bonds, configured_bonds):
    bonds_to_be_removed = set()
    bonds_to_be_added_or_edited = {}

    for bond, attrs in six.iteritems(bonds):
        if 'remove' in attrs:
            bonds_to_be_removed.add(bond)
        elif attrs != configured_bonds.get(bond):
            bonds_to_be_added_or_edited[bond] = attrs

    return bonds_to_be_added_or_edited, bonds_to_be_removed


def _setup_ovs_devices(nets_to_be_added, nets_to_be_removed):
    ovsdb = driver.create()

    with Setup(ovsdb) as s:
        s.remove_nets(nets_to_be_removed)
        s.add_nets(nets_to_be_added)

    with ovsdb.transaction() as t:
        t.add(*_cleanup_unused_bridges(ovsdb))


# TODO: We could move all setup() code into __init__ and __exit__.
class Setup(object):
    def __init__(self, ovsdb):
        self._ovsdb = ovsdb
        self._transaction = self._ovsdb.transaction()
        self._bridges_by_sb = info.OvsInfo().bridges_by_sb

    def __enter__(self):
        return self

    def __exit__(self, type, value, traceback):
        if type is None:
            self._transaction.commit()
        else:
            six.reraise(type, value, traceback)

    def remove_nets(self, nets):
        for net in nets:
            self._remove_net(net)

    def _remove_net(self, net):
        self._transaction.add(self._ovsdb.del_port(net))

    def add_nets(self, nets):
        for net, attrs in six.iteritems(nets):
            sb = attrs['nic']
            bridge = self._bridges_by_sb.get(sb) or self._create_bridge(sb)

            self._create_nb(bridge, net)
            vlan = attrs.get('vlan')
            if vlan is not None:
                self._set_vlan(net, vlan)

    def _create_nb(self, bridge, port):
        self._transaction.add(self._ovsdb.add_port(bridge, port))
        self._transaction.add(self._ovsdb.set_port_attr(
            port, 'other_config:vdsm_level', info.NORTHBOUND))
        self._transaction.add(self._ovsdb.set_interface_attr(
            port, 'type', 'internal'))

    def _set_vlan(self, net, vlan):
        self._transaction.add(self._ovsdb.set_port_attr(net, 'tag', vlan))

    def _create_bridge(self, sb):
        bridge = self._create_br_name()
        self._transaction.add(self._ovsdb.add_br(bridge))
        self._create_sb(bridge, sb)
        self._bridges_by_sb[sb] = bridge
        return bridge

    @staticmethod
    def _create_br_name():
        return random_iface_name(prefix=BRIDGE_PREFIX)

    def _create_sb(self, bridge, port):
        self._transaction.add(self._ovsdb.add_port(bridge, port))
        self._transaction.add(self._ovsdb.set_port_attr(
            port, 'other_config:vdsm_level', info.SOUTHBOUND))


def _cleanup_unused_bridges(ovsdb):
    return [ovsdb.del_br(bridge) for bridge in _unused_bridges()]


def _unused_bridges():
    unused_bridges = set()
    for bridge, attrs in six.iteritems(info.OvsInfo().bridges):
        northbound_ports = info.OvsInfo.northbound_ports(attrs['ports'])
        if (bridge.startswith(BRIDGE_PREFIX) and not list(northbound_ports)):
            unused_bridges.add(bridge)
    return unused_bridges
