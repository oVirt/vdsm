#
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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#
from __future__ import absolute_import

import errno

from vdsm import commands
from vdsm.common import cmdutils

from testlib import VdsmTestCase as TestCaseBase


class TestCommandPath(TestCaseBase):
    def testExisting(self):
        cp = cmdutils.CommandPath('sh', 'utter nonsense', '/bin/sh')
        self.assertEqual(cp.cmd, '/bin/sh')

    def testExistingNotInPaths(self):
        """Tests if CommandPath can find the executable like the 'which' unix
        tool"""
        cp = cmdutils.CommandPath('sh', 'utter nonsense')
        _, stdout, _ = commands.execCmd(['which', 'sh'])
        self.assertIn(cp.cmd.encode(), stdout)

    def testMissing(self):
        NAME = 'nonsense'
        try:
            cmdutils.CommandPath(NAME, 'utter nonsense').cmd
        except OSError as e:
            self.assertEqual(e.errno, errno.ENOENT)
            self.assertIn(NAME, e.strerror)
