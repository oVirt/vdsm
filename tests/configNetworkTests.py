#
# Copyright 2012 IBM, Inc.
# Copyright 2012 Red Hat, Inc.
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
import subprocess
import tempfile
import shutil
import pwd

import configNetwork
from vdsm import netinfo

from testrunner import VdsmTestCase as TestCaseBase
from nose.plugins.skip import SkipTest


class TestconfigNetwork(TestCaseBase):

    def testNicSort(self):
        nics = {'nics_init': ('p33p1', 'eth1', 'lan0', 'em0', 'p331',
                              'Lan1', 'eth0', 'em1', 'p33p2', 'p33p10'),
                'nics_expected': ('Lan1', 'em0', 'em1', 'eth0', 'eth1',
                                  'lan0', 'p33p1', 'p33p10', 'p33p2', 'p331')}

        nics_res = configNetwork.nicSort(nics['nics_init'])
        self.assertEqual(nics['nics_expected'], tuple(nics_res))

    def testIsBridgeNameValid(self):
        invalidBrName = ('-abc', 'abcdefghijklmnop', 'a:b', 'a.b')
        for i in invalidBrName:
            res = configNetwork.isBridgeNameValid(i)
            self.assertEqual(0, res)


class ConfigWriterTests(TestCaseBase):
    INITIAL_CONTENT = '123-testing'
    SOME_GARBAGE = '456'

    def __init__(self, *args, **kwargs):
        TestCaseBase.__init__(self, *args, **kwargs)
        self._tempdir = tempfile.mkdtemp()
        self._files = tuple((os.path.join(self._tempdir, bn), init, makeDirty)
                       for bn, init, makeDirty in
                       (('ifcfg-eth0', self.INITIAL_CONTENT, True),
                        ('ifcfg-eth1', None, True),
                        ('ifcfg-eth2', None, False),
                        ('ifcfg-eth3', self.INITIAL_CONTENT, False),
                       ))

    def __del__(self):
        shutil.rmtree(self._tempdir)

    def _createFiles(self):
        for fn, content, _ in self._files:
            if content is not None:
                file(fn, 'w').write(content)

    def _makeFilesDirty(self):
        for fn, _, makeDirty in self._files:
            if makeDirty:
                file(fn, 'w').write(self.SOME_GARBAGE)

    def _assertFilesRestored(self):
        for fn, content, _ in self._files:
            if content is None:
                self.assertFalse(os.path.exists(fn))
            else:
                restoredContent = file(fn).read()
                self.assertEqual(content, restoredContent)

    def testAtomicRestore(self):
        # a rather ugly stubbing
        oldvals = subprocess.Popen
        subprocess.Popen = lambda x: None

        try:
            cw = configNetwork.ConfigWriter()
            self._createFiles()

            for fn, _, _ in self._files:
                cw._atomicBackup(fn)

            self._makeFilesDirty()

            cw.restoreAtomicBackup()
            self._assertFilesRestored()
        finally:
            subprocess.Popen = oldvals

    def testPersistentBackup(self):
        #after vdsm package is installed, the 'vdsm' account will be created
        #if no 'vdsm' account, we should skip this test
        if 'vdsm' not in [val.pw_name for val in pwd.getpwall()]:
            raise SkipTest("'vdsm' is not in user account database, "
                           "install vdsm package to create the vdsm user")

        # a rather ugly stubbing
        oldvals = (netinfo.NET_CONF_BACK_DIR,
                   os.chown)
        os.chown = lambda *x: 0
        netinfo.NET_CONF_BACK_DIR = os.path.join(self._tempdir, 'netback')

        try:
            cw = configNetwork.ConfigWriter()
            self._createFiles()

            for fn, _, _ in self._files:
                cw._persistentBackup(fn)

            self._makeFilesDirty()

            subprocess.call(['/bin/bash', '../vdsm/vdsm-restore-net-config',
                             '--skip-net-restart'],
                    env={'NET_CONF_BACK_DIR': netinfo.NET_CONF_BACK_DIR,
                         'NET_CONF_DIR': self._tempdir})

            self._assertFilesRestored()
        finally:
            netinfo.NET_CONF_BACK_DIR, os.chown = oldvals
