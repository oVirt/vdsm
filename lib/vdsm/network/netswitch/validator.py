# Copyright 2016-2020 Red Hat, Inc.
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
from vdsm.network.netinfo.cache import NetInfo
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
                    'network "{}"'.format(net_name),
                )

    if len(set(underlying_devices)) < len(underlying_devices):
        raise ne.ConfigNetworkError(
            ne.ERR_BAD_PARAMS,
            'multiple networks/similar vlans cannot be'
            ' defined on a single underlying device. '
            'kernel networks: {}\nrequested networks: {}'.format(
                kernel_config.networks, nets
            ),
        )


class Validator(object):
    def __init__(self, nets, bonds, net_info):
        self._nets = nets
        self._bonds = bonds
        self._net_info = NetInfo(net_info)
        self._desired_config = self._create_desired_config()

    def validate_bond(self, name):
        _BondValidator(
            name, self._bonds[name], self._desired_config, self._net_info
        ).validate()

    def validate_nic_usage(self):
        used_bond_slaves = set()
        for bond_attr in self._desired_config.bonds.values():
            used_bond_slaves |= set(bond_attr['nics'])

        used_net_nics = {
            net_attr['nic']
            for net_attr in self._desired_config.networks.values()
            if net_attr.get('nic')
        }

        shared_nics = used_bond_slaves & used_net_nics
        if shared_nics:
            raise ne.ConfigNetworkError(
                ne.ERR_USED_NIC, f'Nics with multiple usages: {shared_nics}'
            )

    def _create_desired_config(self):
        desired_config = KernelConfig(self._net_info)

        for net_name, net_attr in self._nets.items():
            if 'remove' in net_attr:
                desired_config.removeNetwork(net_name)
            else:
                desired_config.setNetwork(net_name, net_attr)

        for bond_name, bond_attr in self._bonds.items():
            if 'remove' in bond_attr:
                desired_config.removeBonding(bond_name)
            else:
                desired_config.setBonding(bond_name, bond_attr)
        return desired_config


class _BondValidator(object):
    def __init__(self, name, attrs, desired_config, net_info):
        self._name = name
        self._desired_config = desired_config
        self._net_info = net_info

        self._remove = attrs.get('remove')
        self._nics = attrs.get('nics')
        self._options = attrs.get('options')

    def validate(self):
        if self._remove:
            self._validate_bond_removal()
            return

        if self._nics:
            self._validate_bond_addition()
        else:
            raise ne.ConfigNetworkError(
                ne.ERR_BAD_NIC, 'Missing nics attribute'
            )

        if self._options:
            _validate_bond_options(self._options)

    def _validate_bond_removal(self):
        nets_with_bond = {
            net
            for net, attrs in self._desired_config.networks.items()
            if attrs.get('bonding') == self._name
        }

        if nets_with_bond:
            raise ne.ConfigNetworkError(
                ne.ERR_USED_BOND,
                'Cannot remove bonding {}: used by network ({}).'.format(
                    self._name, nets_with_bond
                ),
            )

    def _validate_bond_addition(self):
        for slave in self._nics:
            _validate_nic_exists(slave, self._net_info.nics)
            if dpdk.is_dpdk(slave):
                raise ne.ConfigNetworkError(
                    ne.ERR_BAD_NIC,
                    '%s is a dpdk device and not supported as a slave of bond'
                    % slave,
                )


def validate_network_setup(nets, bonds, net_info):
    kernel_nics = nics()
    kernel_bonds = Bond.bonds()
    current_nets = net_info['networks']
    validator = Validator(nets, bonds, net_info)
    for net, attrs in six.iteritems(nets):
        validate_net_configuration(
            net,
            attrs,
            bonds,
            kernel_bonds,
            kernel_nics,
            current_nets,
            RunningConfig().networks,
        )
    for bond in bonds.keys():
        validator.validate_bond(bond)
    validator.validate_nic_usage()


def validate_net_configuration(
    net,
    netattrs,
    desired_bonds,
    current_bonds,
    current_nics,
    netinfo_networks=None,
    running_config_networks=None,
):
    """Test if network meets logical Vdsm requiremets.

    Bridgeless networks are allowed in order to support Engine requirements.

    Checked by OVS:
        - only one vlan per tag
    """
    _validate_network_remove(
        net, netattrs, netinfo_networks or {}, running_config_networks or {}
    )
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


def _validate_vlan_id(id):
    MAX_ID = 4094

    try:
        vlan_id = int(id)
    except ValueError:
        raise ne.ConfigNetworkError(
            ne.ERR_BAD_VLAN,
            'VLAN id must be a number between 0 and {}'.format(MAX_ID),
        )

    if not 0 <= vlan_id <= MAX_ID:
        raise ne.ConfigNetworkError(
            ne.ERR_BAD_VLAN,
            'VLAN id out of range: %r, must be 0..%s' % (id, MAX_ID),
        )


def _validate_network_remove(
    netname, netattrs, netinfo_networks, running_config_networks
):
    netattrs_set = set(netattrs)
    is_remove = netattrs.get('remove')
    if is_remove and netattrs_set - set(['remove', 'custom']):
        raise ne.ConfigNetworkError(
            ne.ERR_BAD_PARAMS,
            'Cannot specify any attribute when removing (except custom)).',
        )
    if is_remove:
        if (
            netname not in netinfo_networks
            and netname not in running_config_networks
        ):
            raise ne.ConfigNetworkError(
                ne.ERR_BAD_BRIDGE,
                "Cannot delete "
                "network %r: It doesn't exist in the "
                "system" % netname,
            )


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
            'Error parsing bonding options: %r' % bond_options,
        )

    mode = sysfs_options.numerize_bond_mode(mode)
    defaults = sysfs_options.getDefaultBondingOptions(mode)

    for option in bond_options.split():
        key, _ = option.split('=', 1)
        if key not in defaults:
            raise ne.ConfigNetworkError(
                ne.ERR_BAD_BONDING, '%r is not a valid bonding option' % key
            )


def _validate_bond_exists(bond, desired_bonds, running_bonds):
    running_bond = bond in running_bonds
    bond2setup = bond in desired_bonds and 'remove' not in desired_bonds[bond]
    if not running_bond and not bond2setup:
        raise ne.ConfigNetworkError(
            ne.ERR_BAD_BONDING, 'Bond %s does not exist' % bond
        )


def _validate_nic_exists(nic, current_nics):
    if nic not in current_nics:
        raise ne.ConfigNetworkError(
            ne.ERR_BAD_NIC, 'Nic %s does not exist' % nic
        )


def validate_bridge_name(bridge_name):
    if (
        not bridge_name
        or len(bridge_name) > MAX_NAME_LEN
        or set(bridge_name) & ILLEGAL_CHARS
        or bridge_name.startswith('-')
    ):
        raise ne.ConfigNetworkError(
            ne.ERR_BAD_BRIDGE, "Bridge name isn't valid: %r" % bridge_name
        )
