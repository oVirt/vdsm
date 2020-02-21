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

import errno
import signal

from vdsm.common import cmdutils
from vdsm.common import commands
from vdsm.common.compat import subprocess
from vdsm.common.password import ProtectedPassword

from testlib import VdsmTestCase as TestCaseBase


class TestCommandPath(TestCaseBase):
    def testExisting(self):
        cp = cmdutils.CommandPath('sh', 'utter nonsense', '/bin/sh')
        self.assertEqual(cp.cmd, '/bin/sh')

    def testExistingNotInPaths(self):
        """Tests if CommandPath can find the executable like the 'which' unix
        tool"""
        cp = cmdutils.CommandPath('sh', 'utter nonsense')
        stdout = commands.run(['which', 'sh'])
        self.assertIn(cp.cmd.encode(), stdout)

    def testMissing(self):
        NAME = 'nonsense'
        try:
            cmdutils.CommandPath(NAME, 'utter nonsense').cmd
        except OSError as e:
            self.assertEqual(e.errno, errno.ENOENT)
            self.assertIn(NAME, e.strerror)


class List2CmdlineeTests(TestCaseBase):

    def test_simple(self):
        args = ['/usr/bin/dd', 'iflag=direct',
                'if=/dev/a70a4106-24f2-4599-be3e-934fee6e4499/metadata',
                'bs=4096', 'count=1']
        line = ' '.join(args)
        self.assertEqual(cmdutils._list2cmdline(args), line)

    def test_whitespace(self):
        args = ['a b', ' c ', 'd\t', '\ne']
        line = "'a b' ' c ' 'd\t' '\ne'"
        self.assertEqual(cmdutils._list2cmdline(args), line)

    def test_unsafe(self):
        args = [c for c in '><*?[]`$|;&()#$\\"']
        line = ' '.join("'" + c + "'" for c in args)
        self.assertEqual(cmdutils._list2cmdline(args), line)

    def test_safe(self):
        # Stolen from pipes._safechars
        line = ' '.join('%+,-./0123456789:=@ABCDEFGHIJKLMNOPQRSTUVWXYZ_abcdef'
                        'ghijklmnopqrstuvwxyz')
        args = line.split()
        self.assertEqual(cmdutils._list2cmdline(args), line)

    def test_single_quote(self):
        args = ["don't", "try 'this'", "'at home'"]
        line = r"'don'\''t' 'try '\''this'\''' ''\''at home'\'''"
        self.assertEqual(cmdutils._list2cmdline(args), line)

    def test_empty_arg(self):
        self.assertEqual(cmdutils._list2cmdline(['a', '', 'b']), "a '' b")

    def test_empty(self):
        self.assertEqual(cmdutils._list2cmdline([]), "")

    def test_protected_password(self):
        secret = ProtectedPassword("secret!")
        line = "'" + str(secret) + "'"
        self.assertEqual(cmdutils._list2cmdline([secret]), line)


class ExecCmdTest(TestCaseBase):

    def test_exec_cmd_with_no_output(self):
        rc, out, err = cmdutils.exec_cmd(('true',))
        self.assertEqual(rc, 0)
        self.assertEqual(out, b'')
        self.assertEqual(err, b'')

    def test_exec_cmd_with_success_output(self):
        rc, out, err = cmdutils.exec_cmd(('echo', 'hello world'))
        self.assertEqual(rc, 0, err)
        self.assertEqual(out, b'hello world\n')
        self.assertEqual(err, b'')

    def test_exec_cmd_with_error_output(self):
        rc, out, err = cmdutils.exec_cmd(('ls', 'no such prog'))
        self.assertNotEqual(rc, 0)
        self.assertIn(b'No such file or directory', err)
        self.assertEqual(out, b'')

    def test_exec_cmd_with_env(self):
        rc, out, err = cmdutils.exec_cmd(
            ('sh', '-c', 'echo $XXX'), env={'XXX': 'hello'})
        self.assertEqual(rc, 0)
        self.assertEqual(out, b'hello\n')


class TestError(TestCaseBase):

    def test_format(self):
        # Should not raise
        str(cmdutils.Error(["cmd"], 1, "out\n", "err\n"))


class TestReceive(TestCaseBase):

    def test_no_output_success(self):
        p = subprocess.Popen(["true"],
                             stdin=None,
                             stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE)
        received = list(cmdutils.receive(p))
        self.assertEqual(received, [])
        self.assertEqual(p.returncode, 0)

    def test_no_output_error(self):
        p = subprocess.Popen(["false"],
                             stdin=None,
                             stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE)
        received = list(cmdutils.receive(p))
        self.assertEqual(received, [])
        self.assertEqual(p.returncode, 1)

    def test_stdout(self):
        p = subprocess.Popen(["echo", "output"],
                             stdin=None,
                             stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE)
        received = list(cmdutils.receive(p))
        self.assertEqual(received, [(cmdutils.OUT, b"output\n")])
        self.assertEqual(p.returncode, 0)

    def test_stderr(self):
        p = subprocess.Popen(["sh", "-c", "echo error >/dev/stderr"],
                             stdin=None,
                             stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE)
        received = list(cmdutils.receive(p))
        self.assertEqual(received, [(cmdutils.ERR, b"error\n")])
        self.assertEqual(p.returncode, 0)

    def test_both_stdout_stderr(self):
        p = subprocess.Popen(
            ["sh", "-c", "echo output; echo error >/dev/stderr;"],
            stdin=None,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE)
        received = list(cmdutils.receive(p))
        self.assertEqual(sorted(received), sorted([
            (cmdutils.OUT, b"output\n"), (cmdutils.ERR, b"error\n")
        ]))
        self.assertEqual(p.returncode, 0)

    def test_timeout(self):
        p = subprocess.Popen(["sleep", "1"],
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
        p = subprocess.Popen(["yes"],
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
        p = subprocess.Popen(["sleep", "1"],
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
        p = subprocess.Popen(cmd, stdin=None, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE)
        try:
            with self.assertRaises(cmdutils.TimeoutExpired):
                for _ in cmdutils.receive(p, 0.5):
                    pass
        finally:
            p.kill()
            p.wait()

    def test_terminate(self):
        p = subprocess.Popen(["sleep", "1"],
                             stdin=None,
                             stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE)
        p.terminate()
        list(cmdutils.receive(p))
        self.assertEqual(p.returncode, -signal.SIGTERM)

    def test_kill(self):
        p = subprocess.Popen(["sleep", "1"],
                             stdin=None,
                             stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE)
        p.kill()
        list(cmdutils.receive(p))
        self.assertEqual(p.returncode, -signal.SIGKILL)
