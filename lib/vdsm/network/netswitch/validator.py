# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

from vdsm.network import errors as ne
from vdsm.network.kernelconfig import KernelConfig
from vdsm.network.link.bond import sysfs_options
from vdsm.network.netconfpersistence import RunningConfig
from vdsm.network.netinfo.cache import NetInfo


MAX_NAME_LEN = 15
ILLEGAL_CHARS = frozenset(':. \t')


class Validator(object):
    def __init__(self, nets, bonds, net_info):
        self._nets = nets
        self._bonds = bonds
        self._net_info = NetInfo(net_info)
        self._desired_config = self._create_desired_config()
        self._running_config = RunningConfig()

    def validate_bond(self, name):
        _BondValidator(
            name, self._bonds[name], self._desired_config, self._net_info
        ).validate()

    def validate_net(self, name):
        _NetValidator(
            name,
            self._nets[name],
            self._desired_config,
            self._net_info,
            self._running_config,
        ).validate()

    def validate_southbound_devices_usages(self):
        underlying_devices = []
        for (net_name, net_attrs) in self._desired_config.networks.items():
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
                        f'network "{net_name}"',
                    )

        if len(set(underlying_devices)) < len(underlying_devices):
            raise ne.ConfigNetworkError(
                ne.ERR_BAD_PARAMS,
                'multiple networks/similar vlans cannot be'
                ' defined on a single underlying device. '
                f'kernel networks: {self._desired_config.networks}\n'
                f'requested networks: {self._nets}',
            )

    def validate_nic_usage(self):
        used_bond_slaves = set()
        for bond_attr in self._desired_config.bonds.values():
            used_bond_slaves |= set(bond_attr['nics'])

        used_net_nics = {
            net_attr['nic']
            for net_attr in self._desired_config.networks.values()
            if net_attr.get('nic')
        }

        # This should care only about vlans that are not owned by vdsm
        used_unmanaged_vlan_nics = self._get_unmanaged_vlan_nics()

        shared_by_bond_and_nets = used_bond_slaves & used_net_nics
        shared_by_vlan_and_bond = used_bond_slaves & used_unmanaged_vlan_nics

        shared_nics = shared_by_vlan_and_bond or shared_by_bond_and_nets
        if shared_nics:
            raise ne.ConfigNetworkError(
                ne.ERR_USED_NIC, f'Nics with multiple usages: {shared_nics}'
            )

    def _get_unmanaged_vlan_nics(self):
        southbounds_managed_by_vdsm = {
            net_attr['southbound']
            for net_attr in self._net_info.networks.values()
        }
        return {
            vlan_attr['iface']
            for vlan, vlan_attr in self._net_info.vlans.items()
            if vlan not in southbounds_managed_by_vdsm
        }

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


class _NetValidator(object):
    def __init__(self, name, attrs, desired_config, net_info, running_config):
        self._name = name
        self._attrs = attrs
        self._desired_config = desired_config
        self._net_info = net_info
        self._running_config = running_config

        self._remove = attrs.get('remove')
        self._nic = attrs.get('nic')
        self._bond = attrs.get('bonding')
        self._vlan = attrs.get('vlan')
        self._bridged = attrs.get('bridged')

    def validate(self):
        if self._remove:
            self._validate_network_remove()
            self._validate_network_exists()
            return

        if self._vlan:
            _validate_vlan_id(self._vlan)

        # Missing nic or bond is fine as we support nicless networks
        if self._nic:
            _validate_nic_exists(self._nic, self._net_info.nics)
        elif self._bond:
            self._validate_bond_exists()

        if self._bridged:
            validate_bridge_name(self._name)

    def _validate_network_remove(self):
        net_attrs_set = set(self._attrs) - {'remove', 'custom'}
        if net_attrs_set:
            raise ne.ConfigNetworkError(
                ne.ERR_BAD_PARAMS,
                'Cannot specify any attribute when removing (except custom)).',
            )

    def _validate_network_exists(self):
        if (
            self._name not in self._net_info.networks
            and self._name not in self._running_config.networks
        ):
            raise ne.ConfigNetworkError(
                ne.ERR_BAD_BRIDGE,
                'Cannot delete '
                f'network {self._name}: It doesn\'t exist in the system',
            )

    def _validate_bond_exists(self):
        if self._bond not in self._desired_config.bonds:
            raise ne.ConfigNetworkError(
                ne.ERR_BAD_BONDING, f'Bond {self._bond} does not exist'
            )


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


def validate_network_setup(nets, bonds, net_info):
    validator = Validator(nets, bonds, net_info)
    validator.validate_southbound_devices_usages()
    for net in nets.keys():
        validator.validate_net(net)
    for bond in bonds.keys():
        validator.validate_bond(bond)
    validator.validate_nic_usage()


def validate_bridge_name(bridge_name):
    if (
        not bridge_name
        or len(bridge_name) > MAX_NAME_LEN
        or set(bridge_name) & ILLEGAL_CHARS
        or bridge_name.startswith('-')
    ):
        raise ne.ConfigNetworkError(
            ne.ERR_BAD_BRIDGE, f'Bridge name isn\'t valid: {bridge_name}'
        )


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


def _validate_nic_exists(nic, current_nics):
    if nic not in current_nics:
        raise ne.ConfigNetworkError(
            ne.ERR_BAD_NIC, 'Nic %s does not exist' % nic
        )
