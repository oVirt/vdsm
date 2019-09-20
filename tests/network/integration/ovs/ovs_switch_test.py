#
# Copyright 2018 Red Hat, Inc.
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
#

from __future__ import absolute_import
from __future__ import division

import unittest

from vdsm.network.ovs import info as ovs_info
from vdsm.network.ovs import switch as ovs_switch


class MockedOvsInfo(ovs_info.OvsInfo):
    def __init__(self):
        self._bridges = {}
        self._bridges_by_sb = {}
        self._northbounds_by_sb = {}


class SetupTransactionTests(unittest.TestCase):
    def test_dry_run(self):
        ovs_info = MockedOvsInfo()
        net_rem_setup = ovs_switch.NetsRemovalSetup(ovs_info)
        net_rem_setup.prepare_setup({})
        net_rem_setup.commit_setup()

        net_add_setup = ovs_switch.NetsAdditionSetup(ovs_info)
        net_add_setup.prepare_setup({})
        net_add_setup.commit_setup()
