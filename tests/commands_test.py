#
# Copyright 2012-2016 Red Hat, Inc.
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
import os
import os.path
import six
import sys
import threading
import time

from vdsm import constants
from vdsm import commands

from testlib import permutations, expandPermutations
from testlib import VdsmTestCase as TestCaseBase
from testValidation import checkSudo
from testValidation import stresstest


@expandPermutations
class ExecCmdTest(TestCaseBase):
    CMD_TYPES = ((tuple,), (list,), (iter,))

    @permutations(CMD_TYPES)
    def testNormal(self, cmd):
        rc, out, _ = commands.execCmd(cmd(('echo', 'hello world')))
        self.assertEqual(rc, 0)
        self.assertEqual(out[0].decode(), 'hello world')

    @permutations(CMD_TYPES)
    def testIoClass(self, cmd):
        rc, out, _ = commands.execCmd(cmd(('ionice',)), ioclass=2,
                                      ioclassdata=3)
        self.assertEqual(rc, 0)
        self.assertEqual(out[0].decode().strip(), 'best-effort: prio 3')

    @permutations(CMD_TYPES)
    def testNice(self, cmd):
        rc, out, _ = commands.execCmd(cmd(('cat', '/proc/self/stat')), nice=7)
        self.assertEqual(rc, 0)
        self.assertEqual(int(out[0].split()[18]), 7)

    @permutations(CMD_TYPES)
    def testSetSid(self, cmd):
        cmd_args = (sys.executable, '-c',
                    'from __future__ import print_function;import os;'
                    'print(os.getsid(os.getpid()))')
        rc, out, _ = commands.execCmd(cmd(cmd_args), setsid=True)
        self.assertNotEquals(int(out[0]), os.getsid(os.getpid()))

    @permutations(CMD_TYPES)
    def testSudo(self, cmd):
        checkSudo(['echo'])
        rc, out, _ = commands.execCmd(cmd(('grep',
                                      'Uid', '/proc/self/status')),
                                      sudo=True)
        self.assertEqual(rc, 0)
        self.assertEqual(int(out[0].split()[2]), 0)


class ExecCmdStressTest(TestCaseBase):

    CONCURRENCY = 50
    FUNC_DELAY = 0.01
    FUNC_CALLS = 40
    BLOCK_SIZE = 4096
    BLOCK_COUNT = 256

    def setUp(self):
        self.data = None  # Written to process stdin
        self.workers = []
        self.resume = threading.Event()

    @stresstest
    def test_read_stderr(self):
        self.check(self.read_stderr)

    @stresstest
    def test_read_stdout_stderr(self):
        self.check(self.read_stdout_stderr)

    @stresstest
    def test_write_stdin_read_stderr(self):
        self.data = 'x' * self.BLOCK_SIZE * self.BLOCK_COUNT
        self.check(self.write_stdin_read_stderr)

    def check(self, func):
        for i in range(self.CONCURRENCY):
            worker = Worker(self.resume, func, self.FUNC_CALLS,
                            self.FUNC_DELAY)
            self.workers.append(worker)
            worker.start()
        for worker in self.workers:
            worker.wait()
        self.resume.set()
        for worker in self.workers:
            worker.join()
        for worker in self.workers:
            if worker.exc_info:
                t, v, tb = worker.exc_info
                six.reraise(t, v, tb)

    def read_stderr(self):
        args = ['if=/dev/zero',
                'of=/dev/null',
                'bs=%d' % self.BLOCK_SIZE,
                'count=%d' % self.BLOCK_COUNT]
        self.run_dd(args)

    def read_stdout_stderr(self):
        args = ['if=/dev/zero',
                'bs=%d' % self.BLOCK_SIZE,
                'count=%d' % self.BLOCK_COUNT]
        out = self.run_dd(args)
        size = self.BLOCK_SIZE * self.BLOCK_COUNT
        if len(out) < size:
            raise self.failureException("Partial read: %d/%d" % (
                                        len(out), size))

    def write_stdin_read_stderr(self):
        args = ['of=/dev/null',
                'bs=%d' % self.BLOCK_SIZE,
                'count=%d' % self.BLOCK_COUNT]
        self.run_dd(args)

    def run_dd(self, args):
        cmd = [constants.EXT_DD]
        cmd.extend(args)
        rc, out, err = commands.execCmd(cmd, raw=True, data=self.data)
        if rc != 0:
            raise self.failureException("Process failed: rc=%d err=%r" %
                                        (rc, err))
        if err == '':
            raise self.failureException("No data from stderr")
        return out


class Worker(object):

    def __init__(self, resume, func, func_calls, func_delay):
        self.exc_info = None
        self._resume = resume
        self._func = func
        self._func_calls = func_calls
        self._func_delay = func_delay
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run)
        self._thread.daemon = True

    def start(self):
        self._thread.start()

    def wait(self):
        self._ready.wait()

    def join(self):
        self._thread.join()

    def _run(self):
        try:
            self._ready.set()
            self._resume.wait()
            for n in range(self._func_calls):
                self._func()
                time.sleep(self._func_delay)
        except Exception:
            self.exc_info = sys.exc_info()
