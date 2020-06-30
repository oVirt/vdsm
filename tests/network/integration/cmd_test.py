#
# Copyright 2017-2020 Red Hat, Inc.
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

from vdsm.network import cmd

from .netintegtestlib import requires_systemdrun


class TestCmd(object):
    def test_exec_sync_with_no_output(self):
        _, out, err = cmd.exec_sync(('true',))
        assert out == ''
        assert err == ''

    def test_exec_sync_with_success_output(self):
        rc, out, err = cmd.exec_sync(('echo', 'hello world'))
        assert rc == 0, err
        assert out == 'hello world\n'
        assert err == ''

    def test_exec_sync_with_error_output(self):
        rc, out, err = cmd.exec_sync(('ls', 'no such prog'))
        assert rc != 0
        assert 'No such file or directory' in err
        assert out == ''

    @requires_systemdrun
    def test_exec_systemd_new_unit(self):
        rc, out, err = cmd.exec_systemd_new_unit(
            ('echo', 'hello world'), slice_name='test-group'
        )
        assert rc == 0, err
        assert out == 'hello world\n'
