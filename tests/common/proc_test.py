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

from vdsm.compat import CPopen as Popen
import vdsm.common

from testlib import VdsmTestCase as TestCaseBase


EXT_SLEEP = "sleep"


class TestPidStat(TestCaseBase):

    def test_without_affinity(self):
        args = [EXT_SLEEP, "3"]
        popen = Popen(args, close_fds=True)
        stats = vdsm.common.proc.pidstat(popen.pid)
        pid = int(stats.pid)
        # procName comes in the format of (procname)
        name = stats.comm
        self.assertEqual(pid, popen.pid)
        self.assertEqual(name, args[0])
        popen.kill()
        popen.wait()
