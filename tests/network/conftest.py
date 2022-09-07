# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

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
