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
from __future__ import division

import subprocess
import time

from vdsm.common import proc

from testlib import VdsmTestCase as TestCaseBase


EXT_SLEEP = "sleep"


class TestPidStat(TestCaseBase):

    def test_without_affinity(self):
        args = [EXT_SLEEP, "3"]
        popen = subprocess.Popen(args, close_fds=True)
        stats = proc.pidstat(popen.pid)
        pid = int(stats.pid)
        # procName comes in the format of (procname)
        name = stats.comm
        self.assertEqual(pid, popen.pid)
        self.assertEqual(name, args[0])
        popen.kill()
        popen.wait()


class TestPgrep(TestCaseBase):
    def test(self):
        sleepProcs = []
        try:
            for i in range(3):
                popen = subprocess.Popen([EXT_SLEEP, "3"])
                sleepProcs.append(popen)
            # There is no guarantee which process run first after forking a
            # child process, make sure all the children are runing before we
            # look for them.
            time.sleep(0.5)
            pids = proc.pgrep(EXT_SLEEP)
            for popen in sleepProcs:
                self.assertIn(popen.pid, pids)
        finally:
            for popen in sleepProcs:
                popen.kill()
                popen.wait()
