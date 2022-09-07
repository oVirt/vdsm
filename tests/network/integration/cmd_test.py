# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from vdsm.network import cmd


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
