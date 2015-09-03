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

from testrunner import VdsmTestCase


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


class TasksetTests(VdsmTestCase):

    CPU_LIST = ['1', '2']

    def test_defaults(self):
        cmd = cmdutils.taskset(['a', 'b'], self.CPU_LIST)
        res = [constants.EXT_TASKSET, '--cpu-list', '1,2', 'a', 'b']
        self.assertEqual(cmd, res)
