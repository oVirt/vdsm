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

import pytest

from vdsm.network import errors as ne
from vdsm.network.link import validator


BOND_NAME = 'bond_name'
NETWORK1_NAME = 'test-network1'
VLANID = 10


class TestBondNameValidation:

    INVALID_BOND_NAMES = ('bond', 'bond bad', 'jamesbond007')

    def test_name_validation_of_net_sb_bond(self):
        NETSETUP = {NETWORK1_NAME: {'bonding': BOND_NAME}}
        assert validator.validate_bond_names(NETSETUP, {}) is None

    def test_name_validation_of_created_bond(self):
        BONDSETUP = {BOND_NAME: {}}
        assert validator.validate_bond_names({}, BONDSETUP) is None

    def test_bad_name_validation_of_net_sb_bond_fails(self):
        for bond_name in self.INVALID_BOND_NAMES:
            self._test_bad_name_validation_fails(
                {NETWORK1_NAME: {'bonding': bond_name}}, {}
            )

    def test_bad_name_validation_of_created_bond_fails(self):
        for bond_name in self.INVALID_BOND_NAMES:
            self._test_bad_name_validation_fails({}, {bond_name: {}})

    def _test_bad_name_validation_fails(self, nets, bonds):
        with pytest.raises(ne.ConfigNetworkError) as cne:
            validator.validate_bond_names(nets, bonds)
        assert cne.value.errCode == ne.ERR_BAD_BONDING


class TestBondConfigValidation:
    def test_bond_without_nics_fails(self):
        with pytest.raises(ne.ConfigNetworkError) as cne:
            validator.validate_bond_configuration({BOND_NAME: {'nics': []}})
        assert cne.value.errCode == ne.ERR_BAD_PARAMS


class TestVlanConfigValidation:
    def test_vlan_without_sb_device_fails(self):
        with pytest.raises(ne.ConfigNetworkError) as cne:
            validator.validate_vlan_configuration(
                {NETWORK1_NAME: {'vlan': VLANID}}
            )
        assert cne.value.errCode == ne.ERR_BAD_VLAN
