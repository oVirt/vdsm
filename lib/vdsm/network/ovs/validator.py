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

import six

from vdsm.network import errors as ne
from vdsm.network.link import dpdk


def validate_net_configuration(
        net, netattrs, desired_bonds, current_bonds, current_nics):
    """Test if network meets logical Vdsm requiremets.

    Bridgeless networks are allowed in order to support Engine requirements.

    Checked by OVS:
        - only one vlan per tag
    """
    nic = netattrs.get('nic')
    bond = netattrs.get('bonding')
    vlan = netattrs.get('vlan')

    if vlan is None:
        if nic:
            _validate_nic_exists(nic, current_nics)
        if bond:
            _validate_bond_exists(bond, desired_bonds, current_bonds)


def validate_bond_configuration(
        bond, bondattrs, desired_nets, current_nets, current_nics):
    if 'remove' in bondattrs:
        _validate_bond_removal(bond, desired_nets, current_nets)
    elif 'nics' in bondattrs:
        _validate_bond_addition(bondattrs['nics'], current_nics)
    else:
        raise ne.ConfigNetworkError(ne.ERR_BAD_NIC, 'Missing nics attribute')


def validate_nic_usage(req_nets, req_bonds,
                       kernel_nets_nics, kernel_bonds_slaves):
    request_bonds_slaves = set()
    for bond_attr in six.itervalues(req_bonds):
        if 'remove' in bond_attr:
            continue
        request_bonds_slaves |= set(bond_attr['nics'])

    request_nets_nics = set()
    for net_attr in six.itervalues(req_nets):
        if 'remove' in net_attr:
            continue
        request_nets_nics |= set([net_attr.get('nic')] or [])

    shared_nics = ((request_bonds_slaves | kernel_bonds_slaves) &
                   (request_nets_nics | kernel_nets_nics))
    if shared_nics:
        raise ne.ConfigNetworkError(
            ne.ERR_USED_NIC, 'Nics with multiple usages: %s' % shared_nics)


def _validate_bond_exists(bond, desired_bonds, running_bonds):
    running_bond = bond in running_bonds
    bond2setup = bond in desired_bonds and 'remove' not in desired_bonds[bond]
    if not running_bond and not bond2setup:
        raise ne.ConfigNetworkError(
            ne.ERR_BAD_BONDING, 'Bond %s does not exist' % bond)


def _validate_bond_addition(nics, current_nics):
    for nic in nics:
        _validate_nic_exists(nic, current_nics)
        if dpdk.is_dpdk(nic):
            raise ne.ConfigNetworkError(
                ne.ERR_BAD_NIC,
                '%s is a dpdk device and not supported as a slave of bond'
                % nic)


def _validate_nic_exists(nic, current_nics):
    if nic not in current_nics:
        raise ne.ConfigNetworkError(
            ne.ERR_BAD_NIC, 'Nic %s does not exist' % nic)


def _validate_bond_removal(bond, desired_nets, current_nets):
    current_nets_with_bond = {net for net, attrs in six.iteritems(current_nets)
                              if attrs['southbound'] == bond}

    add_nets_with_bond = set()
    remove_nets_with_bond = set()
    for net, attrs in six.iteritems(desired_nets):
        if 'remove' in attrs:
            if net in current_nets_with_bond:
                remove_nets_with_bond.add(net)
        elif net in current_nets:
            if net in current_nets_with_bond:
                remove_nets_with_bond.add(net)
            if attrs.get('bonding') == bond:
                add_nets_with_bond.add(net)
        elif attrs.get('bonding') == bond:
                add_nets_with_bond.add(net)

    nets_with_bond = add_nets_with_bond or (current_nets_with_bond -
                                            remove_nets_with_bond)
    if nets_with_bond:
        raise ne.ConfigNetworkError(
            ne.ERR_USED_BOND,
            'Cannot remove bonding {}: used by network ({}).'.format(
                bond, nets_with_bond)
        )
