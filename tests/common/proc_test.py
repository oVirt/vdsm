# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

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
