# Copyright 2014 Red Hat, Inc.
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


import json

from ..netinfo import (BONDING_MASTERS, BONDING_OPT, BONDING_DEFAULTS,
                       bondOpts, realBondOpts)
from ..utils import random_iface_name
from . import expose, ExtraArgsError


def _get_default_bonding_options():
    """
    Return default options per mode, in a dictionary of dictionaries. All keys
    are strings.
    """
    MAX_MODE = 6
    bond_name = random_iface_name()
    opts = {}

    with open(BONDING_MASTERS, 'w') as bonds:
        bonds.write('+' + bond_name)

    try:
        default_mode = bondOpts(bond_name, keys=['mode'])['mode']

        # read default values for all modes
        for mode in range(0, MAX_MODE + 1):
            mode = str(mode)
            with open(BONDING_OPT % (bond_name, 'mode'), 'w') as opt:
                opt.write(mode)

            # only read non-empty options
            opts[mode] = dict(((opt, val) for (opt, val) in
                               realBondOpts(bond_name).iteritems() if val))
            opts[mode]['mode'] = default_mode

    finally:
        with open(BONDING_MASTERS, 'w') as bonds:
            bonds.write('-' + bond_name)

    return opts


@expose('dump-bonding-defaults')
def main(*args):
    """dump-bonding-defaults

    Read bonding option defaults (per mode) and dump them to BONDING_DEFAULTS
    in JSON format.
    """

    if len(args) > 1:
        raise ExtraArgsError()

    with open(BONDING_DEFAULTS, 'w') as defaults:
        json.dump(_get_default_bonding_options(), defaults, sort_keys=True,
                  indent=4, separators=(',', ': '))
