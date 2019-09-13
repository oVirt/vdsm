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
from vdsm.network.configurators import RunningConfig
from vdsm.network.kernelconfig import KernelConfig
from vdsm.network.link import dpdk
from vdsm.network.link.bond import Bond
from vdsm.network.link.bond import sysfs_options
from vdsm.network.netinfo.nics import nics


MAX_NAME_LEN = 15
ILLEGAL_CHARS = frozenset(':. \t')


def validate_southbound_devices_usages(nets, ni):
    kernel_config = KernelConfig(ni)

    for requested_net, net_info in six.viewitems(nets):
        if 'remove' in net_info:
            kernel_config.removeNetwork(requested_net)

    for requested_net, net_info in six.viewitems(nets):
        if 'remove' in net_info:
            continue
        kernel_config.setNetwork(requested_net, net_info)

    underlying_devices = []
    for net_name, net_attrs in six.viewitems(kernel_config.networks):
        vlan = net_attrs.get('vlan')
        if 'bonding' in net_attrs:
            underlying_devices.append((net_attrs['bonding'], vlan))
        elif 'nic' in net_attrs:
            underlying_devices.append((net_attrs['nic'], vlan))
        else:
            if not net_attrs['bridged']:
                raise ne.ConfigNetworkError(
                    ne.ERR_BAD_PARAMS,
                    'southbound device not specified for non-bridged '
                    'network "{}"'.format(net_name))

    if len(set(underlying_devices)) < len(underlying_devices):
        raise ne.ConfigNetworkError(
            ne.ERR_BAD_PARAMS,
            'multiple networks/similar vlans cannot be'
            ' defined on a single underlying device. '
            'kernel networks: {}\nrequested networks: {}'.format(
                kernel_config.networks,
                nets))


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


def validate_network_setup(nets, bonds, net_info):
    kernel_nics = nics()
    kernel_bonds = Bond.bonds()
    for net, attrs in six.iteritems(nets):
        validate_net_configuration(
            net,
            attrs,
            bonds,
            kernel_bonds,
            kernel_nics,
            net_info['networks'],
            RunningConfig().networks)
    for bond, attrs in six.iteritems(bonds):
        validate_bond_configuration(
            bond, attrs, nets, net_info['networks'], kernel_nics)


def validate_net_configuration(
        net, netattrs, desired_bonds, current_bonds, current_nics,
        netinfo_networks=None, running_config_networks=None):
    """Test if network meets logical Vdsm requiremets.

    Bridgeless networks are allowed in order to support Engine requirements.

    Checked by OVS:
        - only one vlan per tag
    """
    _validate_network_remove(net,
                             netattrs,
                             netinfo_networks or {},
                             running_config_networks or {})
    nic = netattrs.get('nic')
    bond = netattrs.get('bonding')
    vlan = netattrs.get('vlan')
    bridged = netattrs.get('bridged')

    if vlan is None:
        if nic:
            _validate_nic_exists(nic, current_nics)
        if bond:
            _validate_bond_exists(bond, desired_bonds, current_bonds)
    else:
        _validate_vlan_id(vlan)

    if bridged:
        validate_bridge_name(net)


def validate_bond_configuration(
        bond, bondattrs, desired_nets, current_nets, current_nics):
    if 'remove' in bondattrs:
        _validate_bond_removal(bond, desired_nets, current_nets)
    elif 'nics' in bondattrs:
        _validate_bond_addition(bondattrs['nics'], current_nics)
    else:
        raise ne.ConfigNetworkError(ne.ERR_BAD_NIC, 'Missing nics attribute')

    if 'options' in bondattrs:
        _validate_bond_options(bondattrs['options'])


def _validate_vlan_id(id):
    MAX_ID = 4094

    try:
        vlan_id = int(id)
    except ValueError:
        raise ne.ConfigNetworkError(
            ne.ERR_BAD_VLAN,
            'VLAN id must be a number between 0 and {}'.format(MAX_ID)
        )

    if not 0 <= vlan_id <= MAX_ID:
        raise ne.ConfigNetworkError(
            ne.ERR_BAD_VLAN,
            'VLAN id out of range: %r, must be 0..%s' % (id, MAX_ID)
        )


def _validate_network_remove(netname,
                             netattrs,
                             netinfo_networks,
                             running_config_networks):
    netattrs_set = set(netattrs)
    is_remove = netattrs.get('remove')
    if is_remove and netattrs_set - set(['remove', 'custom']):
        raise ne.ConfigNetworkError(
            ne.ERR_BAD_PARAMS,
            'Cannot specify any attribute when removing (except custom)).'
        )
    if is_remove:
        if (
                netname not in netinfo_networks and
                netname not in running_config_networks
        ):
            raise ne.ConfigNetworkError(ne.ERR_BAD_BRIDGE,
                                        "Cannot delete "
                                        "network %r: It doesn't exist in the "
                                        "system" % netname)


def _validate_bond_options(bond_options):
    mode = 'balance-rr'
    try:
        for option in bond_options.split():
            key, value = option.split('=', 1)
            if key == 'mode':
                mode = value
    except ValueError:
        raise ne.ConfigNetworkError(
            ne.ERR_BAD_BONDING,
            'Error parsing bonding options: %r' % bond_options
        )

    mode = sysfs_options.numerize_bond_mode(mode)
    defaults = sysfs_options.getDefaultBondingOptions(mode)

    for option in bond_options.split():
        key, _ = option.split('=', 1)
        if key not in defaults:
            raise ne.ConfigNetworkError(
                ne.ERR_BAD_BONDING, '%r is not a valid bonding option' % key)


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


def _validate_nic_exists(nic, current_nics):
    if nic not in current_nics:
        raise ne.ConfigNetworkError(
            ne.ERR_BAD_NIC, 'Nic %s does not exist' % nic)


def validate_bridge_name(bridge_name):
    if (
            not bridge_name or
            len(bridge_name) > MAX_NAME_LEN or
            set(bridge_name) & ILLEGAL_CHARS or
            bridge_name.startswith('-')
    ):
        raise ne.ConfigNetworkError(
            ne.ERR_BAD_BRIDGE,
            "Bridge name isn't valid: %r" % bridge_name)
