# Copyright 2017 Red Hat, Inc.
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

import re

import six

from vdsm.network import errors as ne


def validate(nets, bonds):
    validate_bond_names(nets, bonds)
    validate_bond_configuration(bonds)
    validate_vlan_configuration(nets)


def validate_bond_names(nets, bonds):
    bad_bond_names = {
        bond for bond in bonds if not re.match(r'^bond\w+$', bond)
    }
    bad_bond_names |= {
        net_attrs['bonding']
        for net_attrs in six.viewvalues(nets)
        if 'bonding' in net_attrs
        and not re.match(r'^bond\w+$', net_attrs['bonding'])
    }

    if bad_bond_names:
        raise ne.ConfigNetworkError(
            ne.ERR_BAD_BONDING,
            'bad bond name(s): {}'.format(', '.join(bad_bond_names)),
        )


def validate_bond_configuration(bonds):
    for bond_name, bond_attrs in six.viewitems(bonds):
        if bond_attrs.get('remove', False):
            continue

        nics = bond_attrs.get('nics', [])
        if not nics:
            raise ne.ConfigNetworkError(
                ne.ERR_BAD_PARAMS,
                '{}: Must specify ' 'nics for bonding'.format(bond_name),
            )


def validate_vlan_configuration(nets):
    for net_name, net_attrs in six.viewitems(nets):
        if net_attrs.get('remove', False):
            continue

        if 'vlan' in net_attrs:
            if 'nic' not in net_attrs and 'bonding' not in net_attrs:
                raise ne.ConfigNetworkError(
                    ne.ERR_BAD_VLAN,
                    '{}: vlan device requires south'
                    'bound device'.format(net_name),
                )
