#
# Copyright 2012 Red Hat, Inc.
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
from time import sleep
import os

from testrunner import VdsmTestCase as TestCaseBase
import zombieReaper
from vdsm.betterPopen import BetterPopen


class zombieReaperTests(TestCaseBase):
    def setUp(self):
        zombieReaper.registerSignalHandler()

    def tearDown(self):
        zombieReaper.unregisterSignalHandler()

    def testProcessDiesAfterBeingTracked(self):
        p = BetterPopen(["sleep", "1"])
        zombieReaper.autoReapPID(p.pid)
        # wait for the grim reaper to arrive
        sleep(4)

        # Throws error because pid is not found or is not child
        self.assertRaises(OSError, os.waitpid, p.pid, os.WNOHANG)

    def testProcessDiedBeforeBeingTracked(self):
        p = BetterPopen(["sleep", "0"])
        # wait for the process to die
        sleep(1)

        zombieReaper.autoReapPID(p.pid)

        # Throws error because pid is not found or is not child
        self.assertRaises(OSError, os.waitpid, p.pid, os.WNOHANG)
