# Copyright 2014-2017 Red Hat, Inc.
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
import json
from contextlib import contextmanager
from functools import partial

from vdsm.network.link.bond import sysfs_options as bond_options
from vdsm.network.link.iface import random_iface_name
from vdsm.network.netinfo.bonding import (
    BONDING_MASTERS, BONDING_OPT, BONDING_DEFAULTS, BONDING_NAME2NUMERIC_PATH,
    bond_opts_name2numeric_filtered)
from . import expose, ExtraArgsError

_MAX_BONDING_MODES = 6


@expose('dump-bonding-options')
def main(*args):
    """dump-bonding-options

    Two actions are taken:
    - Read bonding option defaults (per mode) and dump them to
      BONDING_DEFAULTS in JSON format.
    - Read bonding option possible values (per mode) and dump them to
      BONDING_NAME2NUMERIC_PATH in JSON format.
    """

    if len(args) > 1:
        raise ExtraArgsError()

    jdump = partial(json.dump,
                    sort_keys=True, indent=4, separators=(',', ': '))
    with open(BONDING_DEFAULTS, 'w') as f:
        jdump(_get_default_bonding_options(), f)

    with open(BONDING_NAME2NUMERIC_PATH, 'w') as f:
        jdump(_get_bonding_options_name2numeric(), f)


def _get_default_bonding_options():
    """
    Return default options per mode, in a dictionary of dictionaries. All keys
    are strings.
    """
    bond_name = random_iface_name()
    with _bond_device(bond_name):
        default_mode = bond_options.properties(bond_name, ('mode',))['mode']

    # read default values for all modes
    opts = {}
    for mode in range(_MAX_BONDING_MODES + 1):
        mode = str(mode)
        # The bond is created per mode to resolve an EBUSY error
        # that appears randomly when changing bond mode and modifying its
        # attributes. (Seen only on CI runs)
        with _bond_device(bond_name, mode):
            opts[mode] = bond_options.properties(
                bond_name,
                filter_out_properties=bond_options.EXCLUDED_BONDING_ENTRIES)
            opts[mode]['mode'] = default_mode

    return opts


def _get_bonding_options_name2numeric():
    """
    Return a map of options values per mode, in a dictionary of dictionaries.
    All keys are strings.
    """
    bond_name = random_iface_name()
    opts = {}
    for mode in range(_MAX_BONDING_MODES + 1):
        mode = str(mode)
        # The bond is created per mode to resolve an EBUSY error
        # that appears randomly when changing bond mode and modifying its
        # attributes. (Seen only on CI runs)
        with _bond_device(bond_name, mode):
            opts[mode] = bond_opts_name2numeric_filtered(bond_name)

    return opts


@contextmanager
def _bond_device(bond_name, mode=None):
    with open(BONDING_MASTERS, 'w') as bonds:
        bonds.write('+' + bond_name)

    if mode is not None:
        _change_mode(bond_name, mode)
    try:
        yield
    finally:
        with open(BONDING_MASTERS, 'w') as bonds:
            bonds.write('-' + bond_name)


def _change_mode(bond_name, mode):
    with open(BONDING_OPT % (bond_name, 'mode'), 'w') as opt:
        opt.write(mode)
