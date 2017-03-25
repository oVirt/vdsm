#
# Copyright 2015 Red Hat, Inc.
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

from vdsm import cmdutils
from vdsm import constants

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


class List2CmdlineeTests(VdsmTestCase):

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
