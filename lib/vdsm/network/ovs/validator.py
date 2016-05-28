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

import six

from vdsm.network import errors as ne


def validate_net_configuration(net, attrs, to_be_configured_bonds,
                               running_bonds, kernel_nics):
    """Test if network meets logical Vdsm requiremets.

    Bridgeless networks are allowed in order to support Engine requirements.

    Checked by OVS:
        - only one vlan per tag
    """
    nic = attrs.get('nic')
    bond = attrs.get('bonding')
    vlan = attrs.get('vlan')

    if vlan is None:
        if nic and nic not in kernel_nics:
            raise ne.ConfigNetworkError(
                ne.ERR_BAD_NIC, 'Nic %s does not exist' % nic)
        if bond and (bond not in running_bonds and
                     bond not in to_be_configured_bonds):
            raise ne.ConfigNetworkError(
                ne.ERR_BAD_BONDING, 'Bond %s does not exist' % bond)
    else:
        # We do not support ifaceless VLANs in Vdsm, because of legacy VLAN
        # device requires an iface to lie on. It wouldn't be a problem in OVS,
        # where we use tagged fake bridges instead of VLANs. However, ifaceless
        # VLANs are permited in order to keep feature parity.
        # TODO: This limitation could be dropped in the future.
        if not nic and not bond:
            raise ne.ConfigNetworkError(
                ne.ERR_BAD_VLAN, 'Vlan device requires a nic/bond')


def validate_bond_configuration(bond, attrs, nets, running_nets, kernel_nics):
    """Validate bonding parameters which are not verified by OVS itself.

    OVS database commands used for bond's editation do not verify slaves'
    existence and their minimal amount.

    Checked by OVS:
        - bond mode
        - lacp
    """
    if 'remove' in attrs:
        _validate_bond_removal(bond, nets, running_nets)
    else:
        _validate_bond_addition(attrs.get('nics'), kernel_nics)


def _validate_bond_addition(nics, kernel_nics):
    if nics is None or len(nics) < 2:
        raise ne.ConfigNetworkError(
            ne.ERR_BAD_BONDING, 'OVS bond requires at least 2 slaves')
    for nic in nics:
        if nic not in kernel_nics:
            raise ne.ConfigNetworkError(
                ne.ERR_BAD_NIC, 'Nic %s does not exist' % nic)


def _validate_bond_removal(bond, nets, running_nets):
    for net in _nets_with_bond(running_nets, bond):
        to_be_removed = 'remove' in nets.get(net, {})
        if not to_be_removed:
            raise ne.ConfigNetworkError(
                ne.ERR_USED_BOND, 'Cannot remove bonding %s: used by '
                'network %s' % (bond, net))


def _nets_with_bond(running_nets, bond):
    return (net for net, attrs in six.iteritems(running_nets)
            if attrs['bond'] == bond)
