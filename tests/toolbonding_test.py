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


import errno
import os

from nose.plugins.skip import SkipTest

from vdsm.tool.dump_bonding_opts \
    import _get_bonding_options_name2numeric
from testlib import VdsmTestCase as TestCaseBase
from testValidation import ValidateRunningAsRoot
from modprobe import RequireBondingMod


class TestToolBonding(TestCaseBase):
    @ValidateRunningAsRoot
    @RequireBondingMod
    def test_dump_bonding_name2numeric(self):
        BOND_MODE = '0'
        OPT_NAME = 'arp_validate'
        VAL_NAME = 'none'
        VAL_NUMERIC = '0'

        try:
            opt_map = _get_bonding_options_name2numeric()
        except IOError as e:
            if e.errno == errno.EBUSY:
                raise SkipTest('Bond option mapping failed on EBUSY, '
                               'Kernel version: %s' % os.uname()[2])
            raise

        self.assertIn(BOND_MODE, opt_map)
        self.assertIn(OPT_NAME, opt_map[BOND_MODE])
        self.assertIn(VAL_NAME, opt_map[BOND_MODE][OPT_NAME])
        self.assertEqual(opt_map[BOND_MODE][OPT_NAME][VAL_NAME], VAL_NUMERIC)
