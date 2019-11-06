#
# Copyright 2017-2019 Red Hat, Inc.
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

import unittest

from .netintegtestlib import requires_systemdrun

from vdsm.network import cmd


class CmdTest(unittest.TestCase):
    def test_exec_sync_with_no_output(self):
        rc, out, err = cmd.exec_sync(('true',))
        self.assertEqual(out, '')
        self.assertEqual(err, '')

    def test_exec_sync_with_success_output(self):
        rc, out, err = cmd.exec_sync(('echo', 'hello world'))
        self.assertEqual(rc, 0, err)
        self.assertEqual(out, 'hello world\n')
        self.assertEqual(err, '')

    def test_exec_sync_with_error_output(self):
        rc, out, err = cmd.exec_sync(('ls', 'no such prog'))
        self.assertNotEqual(rc, 0)
        self.assertIn('No such file or directory', err)
        self.assertEqual(out, '')

    @requires_systemdrun
    def test_exec_systemd_new_unit(self):
        rc, out, err = cmd.exec_systemd_new_unit(
            ('echo', 'hello world'), slice_name='test-group'
        )
        self.assertEqual(rc, 0, err)
        self.assertEqual(out, 'hello world\n')
