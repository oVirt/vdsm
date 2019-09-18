# Copyright 2016-2018 Red Hat, Inc.
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

import pytest

from .netfunctestlib import NetFuncTestAdapter, NOCHK
from network.nettestlib import dummy_devices
from testlib import mock

from vdsm.network import netrestore
from vdsm.network.link.bond import Bond

BOND_NAME = 'bond1'


adapter = None


@pytest.fixture(scope='module', autouse=True)
def create_adapter(target):
    global adapter
    adapter = NetFuncTestAdapter(target)


@pytest.mark.ovs_switch
class TestRestoreOvsBond(object):
    @mock.patch.object(netrestore, 'NETS_RESTORED_MARK', 'does/not/exist')
    def test_restore_bond(self):
        with dummy_devices(2) as (nic1, nic2):
            BONDCREATE = {BOND_NAME: {'nics': [nic1, nic2], 'switch': 'ovs'}}

            with adapter.reset_persistent_config():
                with adapter.setupNetworks({}, BONDCREATE, NOCHK):
                    adapter.setSafeNetworkConfig()

                    Bond(BOND_NAME).destroy()

                    netrestore.init_nets()

                    adapter.update_netinfo()
                    adapter.assertBond(BOND_NAME, BONDCREATE[BOND_NAME])
