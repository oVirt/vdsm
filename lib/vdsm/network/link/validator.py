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

import re

import six

from vdsm.network import errors as ne


def validate(nets, bonds):
    validate_bond_names(nets, bonds)


def validate_bond_names(nets, bonds):
    bad_bond_names = {bond for bond in bonds if
                      not re.match('^bond[0-9]+$', bond)}
    bad_bond_names |= {net_attrs['bonding'] for net_attrs in
                       six.viewvalues(nets) if 'bonding' in net_attrs and
                       not re.match('^bond[0-9]+$', net_attrs['bonding'])}

    if bad_bond_names:
        raise ne.ConfigNetworkError(ne.ERR_BAD_BONDING,
                                    'bad bond name(s): {}'.format(
                                        ', '.join(bad_bond_names)))
