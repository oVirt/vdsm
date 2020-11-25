# Copyright 2019-2020 Red Hat, Inc.
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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license

from __future__ import absolute_import
from __future__ import division

import logging
import os
import tempfile
from unittest import mock

import pytest

import network as network_tests
from vdsm.network.link.bond import sysfs_options_mapper

from .nettestlib import KernelModule
from .nettestlib import has_sysfs_bond_permission


@pytest.fixture(scope='session')
def bond_module():
    bonding_kmod = KernelModule('bonding')
    bonding_kmod.load()

    if not bonding_kmod.exists():
        return None
    if not has_sysfs_bond_permission():
        logging.warning('No permission on sysfs bonding')
        return None
    return bonding_kmod


@pytest.fixture(scope='session', autouse=True)
def bond_option_mapping(bond_module):
    file1 = tempfile.NamedTemporaryFile()
    file2 = tempfile.NamedTemporaryFile()
    with file1 as f_bond_defaults, file2 as f_bond_name2numeric:

        if bond_module:
            ALTERNATIVE_BONDING_DEFAULTS = f_bond_defaults.name
            ALTERNATIVE_BONDING_NAME2NUMERIC_PATH = f_bond_name2numeric.name
        else:
            ALTERNATIVE_BONDING_DEFAULTS = os.path.join(
                os.path.dirname(network_tests.__file__),
                'static',
                'bonding-defaults.json',
            )
            ALTERNATIVE_BONDING_NAME2NUMERIC_PATH = os.path.join(
                os.path.dirname(network_tests.__file__),
                'static',
                'bonding-name2numeric.json',
            )

        patch_bonding_defaults = mock.patch(
            'vdsm.network.link.bond.sysfs_options.BONDING_DEFAULTS',
            ALTERNATIVE_BONDING_DEFAULTS,
        )
        patch_bonding_name2num = mock.patch(
            'vdsm.network.link.bond.sysfs_options_mapper.'
            'BONDING_NAME2NUMERIC_PATH',
            ALTERNATIVE_BONDING_NAME2NUMERIC_PATH,
        )

        with patch_bonding_defaults, patch_bonding_name2num:
            if bond_module:
                sysfs_options_mapper.dump_bonding_options()
            yield
