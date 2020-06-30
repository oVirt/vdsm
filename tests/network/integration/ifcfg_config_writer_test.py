#
# Copyright 2012 IBM, Inc.
# Copyright 2012-2020 Red Hat, Inc.
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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA
# 02110-1301  USA
#
# Refer to the README and COPYING files for full details of the license
#

import os
import pwd
import shutil
import tempfile

import pytest

from network.compat import mock

from vdsm.network.configurators import ifcfg


INITIAL_CONTENT = '123-testing'
SOME_GARBAGE = '456'


@pytest.fixture
def tempdir():
    temp_dir = tempfile.mkdtemp()
    yield temp_dir
    shutil.rmtree(temp_dir)


@pytest.fixture
def files(tempdir):
    return tuple(
        (os.path.join(tempdir, bn), init, make_dirty)
        for bn, init, make_dirty in (
            ('ifcfg-eth0', INITIAL_CONTENT, True),
            ('ifcfg-eth1', None, True),
            ('ifcfg-eth2', None, False),
            ('ifcfg-eth3', INITIAL_CONTENT, False),
        )
    )


@pytest.fixture
def config_writer():
    return ifcfg.ConfigWriter()


class TestIfcfgConfigWriter(object):
    def _create_files(self, files):
        for fn, content, _ in files:
            if content is not None:
                with open(fn, 'w') as f:
                    f.write(content)

    def _make_files_dirty(self, files):
        for fn, _, make_dirty in files:
            if make_dirty:
                with open(fn, 'w') as f:
                    f.write(SOME_GARBAGE)

    def _assert_files_restored(self, files):
        for fn, content, _ in files:
            if content is None:
                assert not os.path.exists(fn)
            else:
                with open(fn) as f:
                    restored_content = f.read()
                assert content == restored_content

    def test_atomic_restore(self, config_writer, files):
        self._create_files(files)

        for fn, _, _ in files:
            config_writer._atomicBackup(fn)

        self._make_files_dirty(files)

        config_writer.restoreAtomicBackup()
        self._assert_files_restored(files)

    @mock.patch.object(ifcfg, 'ifdown', lambda x: 0)
    @mock.patch.object(ifcfg, '_exec_ifup', lambda *x: 0)
    def test_persistent_backup(self, config_writer, tempdir, files):
        netback_path = os.path.join(tempdir, 'netback')
        ifcfg_prefix = os.path.join(tempdir, 'ifcfg-')
        netback_mock = mock.patch.object(
            ifcfg, 'NET_CONF_BACK_DIR', netback_path
        )
        netdir_mock = mock.patch.object(ifcfg, 'NET_CONF_DIR', tempdir)
        netpref_mock = mock.patch.object(ifcfg, 'NET_CONF_PREF', ifcfg_prefix)
        with netback_mock, netdir_mock, netpref_mock:
            # after vdsm package is installed, the 'vdsm' account will be
            # created if no 'vdsm' account, we should skip this test
            if 'vdsm' not in [val.pw_name for val in pwd.getpwall()]:
                pytest.skip(
                    "'vdsm' is not in user account database, "
                    "install vdsm package to create the vdsm user"
                )

            self._create_files(files)

            for fn, _, _ in files:
                config_writer._persistentBackup(fn)

            self._make_files_dirty(files)

            config_writer.restorePersistentBackup()

            self._assert_files_restored(files)
