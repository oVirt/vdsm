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


class SystemdRunTests(TestCaseBase):

    def test_defaults(self):
        cmd = cmdutils.systemd_run(['a', 'b'])
        res = [cmdutils.SYSTEMD_RUN, 'a', 'b']
        self.assertEqual(cmd, res)

    def test_scope(self):
        cmd = cmdutils.systemd_run(['a', 'b'], scope=True)
        res = [cmdutils.SYSTEMD_RUN, '--scope', 'a', 'b']
        self.assertEqual(cmd, res)

    def test_unit(self):
        cmd = cmdutils.systemd_run(['a', 'b'], unit='unit')
        res = [cmdutils.SYSTEMD_RUN, '--unit=unit', 'a', 'b']
        self.assertEqual(cmd, res)

    def test_slice(self):
        cmd = cmdutils.systemd_run(['a', 'b'], slice='slice')
        res = [cmdutils.SYSTEMD_RUN, '--slice=slice', 'a', 'b']
        self.assertEqual(cmd, res)

    def test_accounting(self):
        accounting = (
            cmdutils.Accounting.CPU,
            cmdutils.Accounting.Memory,
            cmdutils.Accounting.BlockIO,
        )
        cmd = cmdutils.systemd_run(['a', 'b'], accounting=accounting)
        res = [
            cmdutils.SYSTEMD_RUN,
            '--property=CPUAccounting=1',
            '--property=MemoryAccounting=1',
            '--property=BlockIOAccounting=1',
            'a',
            'b',
        ]
        self.assertEqual(cmd, res)


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
