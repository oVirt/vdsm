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

from __future__ import print_function

import io
import os
import signal
import subprocess

import six

from vdsm import cmdutils
from vdsm import commands
from vdsm import constants

from vdsm.common.time import monotonic_time
from vdsm.compat import CPopen

from testValidation import skipif, slowtest
from testlib import VdsmTestCase


class SystemdRunTests(VdsmTestCase):

    def test_defaults(self):
        cmd = cmdutils.systemd_run(['a', 'b'])
        res = [constants.EXT_SYSTEMD_RUN, 'a', 'b']
        self.assertEqual(cmd, res)

    def test_scope(self):
        cmd = cmdutils.systemd_run(['a', 'b'], scope=True)
        res = [constants.EXT_SYSTEMD_RUN, '--scope', 'a', 'b']
        self.assertEqual(cmd, res)

    def test_unit(self):
        cmd = cmdutils.systemd_run(['a', 'b'], unit='unit')
        res = [constants.EXT_SYSTEMD_RUN, '--unit=unit', 'a', 'b']
        self.assertEqual(cmd, res)

    def test_slice(self):
        cmd = cmdutils.systemd_run(['a', 'b'], slice='slice')
        res = [constants.EXT_SYSTEMD_RUN, '--slice=slice', 'a', 'b']
        self.assertEqual(cmd, res)

    def test_accounting(self):
        accounting = (
            cmdutils.Accounting.CPU,
            cmdutils.Accounting.Memory,
            cmdutils.Accounting.BlockIO,
        )
        cmd = cmdutils.systemd_run(['a', 'b'], accounting=accounting)
        res = [
            constants.EXT_SYSTEMD_RUN,
            '--property=CPUAccounting=1',
            '--property=MemoryAccounting=1',
            '--property=BlockIOAccounting=1',
            'a',
            'b',
        ]
        self.assertEqual(cmd, res)


class TasksetTests(VdsmTestCase):

    CPU_LIST = ['1', '2']

    def test_defaults(self):
        cmd = cmdutils.taskset(['a', 'b'], self.CPU_LIST)
        res = [constants.EXT_TASKSET, '--cpu-list', '1,2', 'a', 'b']
        self.assertEqual(cmd, res)


class TestError(VdsmTestCase):

    def test_format(self):
        # Should not raise
        str(cmdutils.Error(["cmd"], 1, "out\n", "err\n"))


class TestReceive(VdsmTestCase):

    def test_no_output_success(self):
        p = CPopen(["true"],
                   stdin=None,
                   stdout=subprocess.PIPE,
                   stderr=subprocess.PIPE)
        received = list(cmdutils.receive(p))
        self.assertEqual(received, [])
        self.assertEqual(p.returncode, 0)

    def test_no_output_error(self):
        p = CPopen(["false"],
                   stdin=None,
                   stdout=subprocess.PIPE,
                   stderr=subprocess.PIPE)
        received = list(cmdutils.receive(p))
        self.assertEqual(received, [])
        self.assertEqual(p.returncode, 1)

    def test_stdout(self):
        p = CPopen(["echo", "output"],
                   stdin=None,
                   stdout=subprocess.PIPE,
                   stderr=subprocess.PIPE)
        received = list(cmdutils.receive(p))
        self.assertEqual(received, [(cmdutils.OUT, b"output\n")])
        self.assertEqual(p.returncode, 0)

    def test_stderr(self):
        p = CPopen(["sh", "-c", "echo error >/dev/stderr"],
                   stdin=None,
                   stdout=subprocess.PIPE,
                   stderr=subprocess.PIPE)
        received = list(cmdutils.receive(p))
        self.assertEqual(received, [(cmdutils.ERR, b"error\n")])
        self.assertEqual(p.returncode, 0)

    def test_both_stdout_stderr(self):
        p = CPopen(["sh", "-c", "echo output; echo error >/dev/stderr;"],
                   stdin=None,
                   stdout=subprocess.PIPE,
                   stderr=subprocess.PIPE)
        received = list(cmdutils.receive(p))
        self.assertEqual(sorted(received), sorted([
            (cmdutils.OUT, b"output\n"), (cmdutils.ERR, b"error\n")
        ]))
        self.assertEqual(p.returncode, 0)

    def test_timeout(self):
        p = CPopen(["sleep", "1"],
                   stdin=None,
                   stdout=subprocess.PIPE,
                   stderr=subprocess.PIPE)
        try:
            with self.assertRaises(cmdutils.TimeoutExpired):
                for _ in cmdutils.receive(p, 0.5):
                    pass
        finally:
            p.kill()
            p.wait()

    def test_timeout_with_data(self):
        p = CPopen(["yes"],
                   stdin=None,
                   stdout=subprocess.PIPE,
                   stderr=subprocess.PIPE)
        try:
            with self.assertRaises(cmdutils.TimeoutExpired):
                for _ in cmdutils.receive(p, 0.5):
                    pass
        finally:
            p.kill()
            p.wait()

    def test_no_fds(self):
        p = CPopen(["sleep", "1"],
                   stdin=None,
                   stdout=None,
                   stderr=None)
        try:
            with self.assertRaises(cmdutils.TimeoutExpired):
                for _ in cmdutils.receive(p, 0.5):
                    pass
        finally:
            p.kill()
            p.wait()

    def test_fds_closed(self):
        cmd = ["python", "-c",
               "import os, time; os.close(1); os.close(2); time.sleep(1)"]
        p = CPopen(cmd, stdin=None, stdout=subprocess.PIPE,
                   stderr=subprocess.PIPE)
        try:
            with self.assertRaises(cmdutils.TimeoutExpired):
                for _ in cmdutils.receive(p, 0.5):
                    pass
        finally:
            p.kill()
            p.wait()

    def test_terminate(self):
        p = CPopen(["sleep", "1"],
                   stdin=None,
                   stdout=subprocess.PIPE,
                   stderr=subprocess.PIPE)
        p.terminate()
        list(cmdutils.receive(p))
        self.assertEqual(p.returncode, -signal.SIGTERM)

    def test_kill(self):
        p = CPopen(["sleep", "1"],
                   stdin=None,
                   stdout=subprocess.PIPE,
                   stderr=subprocess.PIPE)
        p.kill()
        list(cmdutils.receive(p))
        self.assertEqual(p.returncode, -signal.SIGKILL)


class TestRecieveBench(VdsmTestCase):

    COUNT = 1024
    BUFSIZE = 1024**2

    def test_plain_read(self):
        p = CPopen(["dd", "if=/dev/zero", "bs=%d" % self.BUFSIZE,
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
        received_gb = received / float(1024**3)
        print("%.2fg in %.2f seconds (%.2fg/s)"
              % (received_gb, elapsed, received_gb / elapsed), end=" ")
        self.assertEqual(received, self.COUNT * self.BUFSIZE)
        self.assertEqual(p.returncode, 0)

    def test_read(self):
        p = CPopen(["dd", "if=/dev/zero", "bs=%d" % self.BUFSIZE,
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
        received_gb = received / float(1024**3)
        print("%.2fg in %.2f seconds (%.2fg/s)"
              % (received_gb, elapsed, received_gb / elapsed), end=" ")
        self.assertEqual(received, self.COUNT * self.BUFSIZE)
        self.assertEqual(p.returncode, 0)

    def test_write(self):
        p = CPopen(["dd", "of=/dev/null", "bs=%d" % self.BUFSIZE],
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
        sent_gb = sent / float(1024**3)
        print("%.2fg in %.2f seconds (%.2fg/s)"
              % (sent_gb, elapsed, sent_gb / elapsed), end=" ")
        self.assertEqual(p.returncode, 0)

    @skipif(six.PY3, "needs porting to python 3")
    @slowtest
    def test_asyncproc_read(self):
        p = commands.execCmd(["dd", "if=/dev/zero", "bs=%d" % self.BUFSIZE,
                              "count=%d" % self.COUNT],
                             sync=False, raw=True)
        start = monotonic_time()
        p.blocking = True
        received = 0
        while True:
            data = p.stdout.read(self.BUFSIZE)
            if not data:
                break
            received += len(data)
        p.wait()
        elapsed = monotonic_time() - start
        received_gb = received / float(1024**3)
        print("%.2fg in %.2f seconds (%.2fg/s)"
              % (received_gb, elapsed, received_gb / elapsed), end=" ")
        self.assertEqual(received, self.COUNT * self.BUFSIZE)
        self.assertEqual(p.returncode, 0)

    @skipif(six.PY3, "needs porting to python 3")
    @slowtest
    def test_asyncproc_write(self):
        p = commands.execCmd(["dd", "of=/dev/null", "bs=%d" % self.COUNT],
                             sync=False, raw=True)
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
        p.wait()
        elapsed = monotonic_time() - start
        sent_gb = sent / float(1024**3)
        print("%.2fg in %.2f seconds (%.2fg/s)"
              % (sent_gb, elapsed, sent_gb / elapsed), end=" ")
        self.assertEqual(p.returncode, 0)
