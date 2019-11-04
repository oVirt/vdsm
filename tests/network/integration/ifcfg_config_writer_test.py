#
# Copyright 2012 IBM, Inc.
# Copyright 2012-2019 Red Hat, Inc.
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

from __future__ import absolute_import
from __future__ import division

import os
import pwd
import shutil
import tempfile

import pytest

from vdsm.network.configurators import ifcfg

from network.compat import mock


@pytest.fixture
def tempdir():
    temp_dir = tempfile.mkdtemp()
    yield temp_dir
    shutil.rmtree(temp_dir)


@pytest.fixture
def files(tempdir):
    return tuple(
        (os.path.join(tempdir, bn), init, makeDirty)
        for bn, init, makeDirty in (
            ('ifcfg-eth0', TestIfcfgConfigWriter.INITIAL_CONTENT, True),
            ('ifcfg-eth1', None, True),
            ('ifcfg-eth2', None, False),
            ('ifcfg-eth3', TestIfcfgConfigWriter.INITIAL_CONTENT, False),
        )
    )


@pytest.fixture
def config_writer():
    return ifcfg.ConfigWriter()


class TestIfcfgConfigWriter(object):
    INITIAL_CONTENT = '123-testing'
    SOME_GARBAGE = '456'

    def _createFiles(self, files):
        for fn, content, _ in files:
            if content is not None:
                with open(fn, 'w') as f:
                    f.write(content)

    def _makeFilesDirty(self, files):
        for fn, _, makeDirty in files:
            if makeDirty:
                with open(fn, 'w') as f:
                    f.write(self.SOME_GARBAGE)

    def _assertFilesRestored(self, files):
        for fn, content, _ in files:
            if content is None:
                assert not os.path.exists(fn)
            else:
                with open(fn) as f:
                    restoredContent = f.read()
                assert content == restoredContent

    def testAtomicRestore(self, config_writer, files):
        self._createFiles(files)

        for fn, _, _ in files:
            config_writer._atomicBackup(fn)

        self._makeFilesDirty(files)

        config_writer.restoreAtomicBackup()
        self._assertFilesRestored(files)

    @mock.patch.object(ifcfg, 'ifdown', lambda x: 0)
    @mock.patch.object(ifcfg, '_exec_ifup', lambda *x: 0)
    def testPersistentBackup(self, config_writer, tempdir, files):

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

            self._createFiles(files)

            for fn, _, _ in files:
                config_writer._persistentBackup(fn)

            self._makeFilesDirty(files)

            config_writer.restorePersistentBackup()

            self._assertFilesRestored(files)
