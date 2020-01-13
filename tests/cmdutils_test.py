#
# Copyright 2015-2017 Red Hat, Inc.
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

from __future__ import print_function

import io
import os
from subprocess import Popen

from vdsm import constants
from vdsm.common import cmdutils
from vdsm.common.compat import subprocess
from vdsm.common.time import monotonic_time
from vdsm.common.units import MiB, GiB

from testlib import VdsmTestCase


class TasksetTests(VdsmTestCase):

    CPU_LIST = ['1', '2']

    def test_defaults(self):
        cmd = cmdutils.taskset(['a', 'b'], self.CPU_LIST)
        res = [constants.EXT_TASKSET, '--cpu-list', '1,2', 'a', 'b']
        self.assertEqual(cmd, res)


class TestRecieveBench(VdsmTestCase):

    COUNT = 1024
    BUFSIZE = MiB

    def test_plain_read(self):
        p = Popen(["dd", "if=/dev/zero", "bs=%d" % self.BUFSIZE,
                   "count=%d" % self.COUNT],
                  stdin=None,
                  stdout=subprocess.PIPE,
                  stderr=subprocess.PIPE)
        start = monotonic_time()
        received = 0
        while True:
            data = os.read(p.stdout.fileno(), self.BUFSIZE)
            if not data:
                break
            received += len(data)
        p.wait()
        elapsed = monotonic_time() - start
        received_gb = received / float(GiB)
        print("%.2fg in %.2f seconds (%.2fg/s)"
              % (received_gb, elapsed, received_gb / elapsed), end=" ")
        self.assertEqual(received, self.COUNT * self.BUFSIZE)
        self.assertEqual(p.returncode, 0)

    def test_read(self):
        p = Popen(["dd", "if=/dev/zero", "bs=%d" % self.BUFSIZE,
                   "count=%d" % self.COUNT],
                  stdin=None,
                  stdout=subprocess.PIPE,
                  stderr=subprocess.PIPE)
        start = monotonic_time()
        received = 0
        for src, data in cmdutils.receive(p, bufsize=self.BUFSIZE):
            if src == cmdutils.OUT:
                received += len(data)
        elapsed = monotonic_time() - start
        received_gb = received / float(GiB)
        print("%.2fg in %.2f seconds (%.2fg/s)"
              % (received_gb, elapsed, received_gb / elapsed), end=" ")
        self.assertEqual(received, self.COUNT * self.BUFSIZE)
        self.assertEqual(p.returncode, 0)

    def test_write(self):
        p = Popen(["dd", "of=/dev/null", "bs=%d" % self.BUFSIZE],
                  stdin=subprocess.PIPE,
                  stdout=None,
                  stderr=subprocess.PIPE)
        start = monotonic_time()
        total = self.COUNT * self.BUFSIZE
        sent = 0
        with io.open("/dev/zero", "rb") as f:
            while sent < total:
                n = min(total - sent, self.BUFSIZE)
                data = f.read(n)
                if not data:
                    raise RuntimeError("/dev/zero closed?!")
                p.stdin.write(data)
                sent += len(data)
        p.stdin.flush()
        p.stdin.close()
        for _, data in cmdutils.receive(p, 10):
            pass
        elapsed = monotonic_time() - start
        sent_gb = sent / float(GiB)
        print("%.2fg in %.2f seconds (%.2fg/s)"
              % (sent_gb, elapsed, sent_gb / elapsed), end=" ")
        self.assertEqual(p.returncode, 0)
