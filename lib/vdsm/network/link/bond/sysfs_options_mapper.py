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

import errno
from glob import iglob
import io
import json
import os

import six

from vdsm import constants
from vdsm.common.cache import memoized

from vdsm.network.link.bond.sysfs_options import bond_opts_read_elements
from vdsm.network.link.bond.sysfs_options import BONDING_OPT
from vdsm.network.link.bond.sysfs_options import EXCLUDED_BONDING_ENTRIES

BONDING_NAME2NUMERIC_PATH = constants.P_VDSM + 'bonding-name2numeric.json'


def bond_opts_name2numeric_filtered(bond):
    """
    Return a dictionary in the same format as _bond_opts_name2numeric().
    Exclude entries that are not bonding options,
    e.g. 'ad_num_ports' or 'slaves'.
    """
    return dict(((opt, val) for (opt, val)
                 in six.iteritems(_bond_opts_name2numeric(bond))
                 if opt not in EXCLUDED_BONDING_ENTRIES))


def get_bonding_option_numeric_val(mode_num, option_name, val_name):
    bond_opts_map = _get_bonding_option_name2numeric()
    opt = bond_opts_map[mode_num].get(option_name, None)
    return opt.get(val_name, None) if opt else None


@memoized
def _get_bonding_option_name2numeric():
    """
    Return options per mode, in a dictionary of dictionaries.
    For each mode, there are options with name values as keys
    and their numeric equivalent.
    """
    with open(BONDING_NAME2NUMERIC_PATH) as f:
        return json.loads(f.read())


def _bond_opts_name2numeric(bond):
    """
    Returns a dictionary of bond option name and a values iterable. E.g.,
    {'mode': ('balance-rr', '0'), 'xmit_hash_policy': ('layer2', '0')}
    """
    bond_mode_path = BONDING_OPT % (bond, 'mode')
    paths = (p for p in iglob(BONDING_OPT % (bond, '*'))
             if p != bond_mode_path)
    opts = {}

    for path in paths:
        elements = bond_opts_read_elements(path)
        if len(elements) == 2:
            opts[os.path.basename(path)] = \
                _bond_opts_name2numeric_scan(path)
    return opts


def _bond_opts_name2numeric_scan(opt_path):
    vals = {}
    with io.open(opt_path, 'wb', buffering=0) as opt_file:
        for numeric_val in range(32):
            name, numeric = _bond_opts_name2numeric_getval(opt_path, opt_file,
                                                           numeric_val)
            if name is None:
                break

            vals[name] = numeric

    return vals


def _bond_opts_name2numeric_getval(opt_path, opt_write_file, numeric_val):
    try:
        opt_write_file.write(str(numeric_val).encode('utf8'))
    except IOError as e:
        if e.errno in (errno.EINVAL, errno.EPERM, errno.EACCES):
            return None, None
        else:
            e.filename = "opt[%s], numeric_val[%s]" % (opt_path, numeric_val)
            raise

    return bond_opts_read_elements(opt_path)
