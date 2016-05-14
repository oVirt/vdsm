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


def validate_bond_configuration(attrs, kernel_nics):
    """Validate bonding parameters which are not verified by OVS itself.

    OVS database commands used for bond's editation do not verify slaves'
    existence and their minimal amount.

    Checked by OVS:
        - bond mode
        - lacp
    """
    nics = attrs.get('nics')

    if nics is None or len(attrs.get('nics')) < 2:
        raise ne.ConfigNetworkError(
            ne.ERR_BAD_BONDING, 'OVS bond requires at least 2 slaves')
    for nic in nics:
        if nic not in kernel_nics:
            raise ne.ConfigNetworkError(
                ne.ERR_BAD_NIC, 'Nic %s does not exist' % nic)
