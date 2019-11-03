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
import subprocess
import tempfile

import pytest

from vdsm.network.configurators import ifcfg

from monkeypatch import MonkeyPatch
from monkeypatch import MonkeyPatchScope
from testlib import VdsmTestCase as TestCaseBase


class TestIfcfgConfigWriter(TestCaseBase):
    INITIAL_CONTENT = '123-testing'
    SOME_GARBAGE = '456'

    def _createFiles(self):
        for fn, content, _ in self._files:
            if content is not None:
                with open(fn, 'w') as f:
                    f.write(content)

    def _makeFilesDirty(self):
        for fn, _, makeDirty in self._files:
            if makeDirty:
                with open(fn, 'w') as f:
                    f.write(self.SOME_GARBAGE)

    def _assertFilesRestored(self):
        for fn, content, _ in self._files:
            if content is None:
                self.assertFalse(os.path.exists(fn))
            else:
                with open(fn) as f:
                    restoredContent = f.read()
                self.assertEqual(content, restoredContent)

    def setUp(self):
        self._tempdir = tempfile.mkdtemp()
        self._files = tuple(
            (os.path.join(self._tempdir, bn), init, makeDirty)
            for bn, init, makeDirty in (
                ('ifcfg-eth0', self.INITIAL_CONTENT, True),
                ('ifcfg-eth1', None, True),
                ('ifcfg-eth2', None, False),
                ('ifcfg-eth3', self.INITIAL_CONTENT, False),
            )
        )
        self._cw = ifcfg.ConfigWriter()

    def tearDown(self):
        shutil.rmtree(self._tempdir)

    @MonkeyPatch(subprocess, 'Popen', lambda x: None)
    def testAtomicRestore(self):
        self._createFiles()

        for fn, _, _ in self._files:
            self._cw._atomicBackup(fn)

        self._makeFilesDirty()

        self._cw.restoreAtomicBackup()
        self._assertFilesRestored()

    @MonkeyPatch(os, 'chown', lambda *x: 0)
    def testPersistentBackup(self):

        with MonkeyPatchScope(
            [
                (
                    ifcfg,
                    'NET_CONF_BACK_DIR',
                    os.path.join(self._tempdir, 'netback'),
                ),
                (ifcfg, 'NET_CONF_DIR', self._tempdir),
                (
                    ifcfg,
                    'NET_CONF_PREF',
                    os.path.join(self._tempdir, 'ifcfg-'),
                ),
                (ifcfg, 'ifdown', lambda x: 0),
                (ifcfg, '_exec_ifup', lambda *x: 0),
            ]
        ):
            # after vdsm package is installed, the 'vdsm' account will be
            # created if no 'vdsm' account, we should skip this test
            if 'vdsm' not in [val.pw_name for val in pwd.getpwall()]:
                pytest.skip(
                    "'vdsm' is not in user account database, "
                    "install vdsm package to create the vdsm user"
                )

            self._createFiles()

            for fn, _, _ in self._files:
                self._cw._persistentBackup(fn)

            self._makeFilesDirty()

            self._cw.restorePersistentBackup()

            self._assertFilesRestored()
