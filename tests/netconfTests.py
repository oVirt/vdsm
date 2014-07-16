#
# Copyright 2012 IBM, Inc.
# Copyright 2012-2014 Red Hat, Inc.
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
import re
import shutil
import subprocess
import tempfile
from xml.dom.minidom import parseString

from vdsm import netinfo
from network.configurators import ifcfg, libvirt

from monkeypatch import MonkeyPatch
from monkeypatch import MonkeyPatchScope
from nose.plugins.skip import SkipTest
from testrunner import VdsmTestCase as TestCaseBase


class ifcfgConfigWriterTests(TestCaseBase):
    INITIAL_CONTENT = '123-testing'
    SOME_GARBAGE = '456'

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

    def assertEqualXml(self, a, b, msg=None):
        """
        Compare two xml strings for equality.
        """

        aXml = parseString(a).toprettyxml()
        bXml = parseString(b).toprettyxml()
        aXmlNrml = re.sub('\n\s*', ' ', aXml).strip()
        bXmlNrml = re.sub('\n\s*', ' ', bXml).strip()
        self.assertEqual(aXmlNrml, bXmlNrml, msg)

    def setUp(self):
        self._tempdir = tempfile.mkdtemp()
        self._files = tuple((os.path.join(self._tempdir, bn), init, makeDirty)
                            for bn, init, makeDirty in
                            (('ifcfg-eth0', self.INITIAL_CONTENT, True),
                             ('ifcfg-eth1', None, True),
                             ('ifcfg-eth2', None, False),
                             ('ifcfg-eth3', self.INITIAL_CONTENT, False),))
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

        with MonkeyPatchScope([
            (netinfo, 'NET_CONF_BACK_DIR',
             os.path.join(self._tempdir, 'netback')),
            (netinfo, 'NET_CONF_DIR', self._tempdir),
            (netinfo, 'NET_CONF_PREF',
             os.path.join(self._tempdir, 'ifcfg-')),
            (ifcfg, 'ifdown', lambda x: 0),
            (ifcfg, 'ifup', lambda *x: 0),
            (libvirt, 'createNetwork', lambda *x: None),
            (libvirt, 'removeNetwork', lambda *x: None),
        ]):
            # after vdsm package is installed, the 'vdsm' account will be
            # created if no 'vdsm' account, we should skip this test
            if 'vdsm' not in [val.pw_name for val in pwd.getpwall()]:
                raise SkipTest("'vdsm' is not in user account database, "
                               "install vdsm package to create the vdsm user")

            self._createFiles()

            for fn, _, _ in self._files:
                self._cw._persistentBackup(fn)

            self._makeFilesDirty()

            self._cw.restorePersistentBackup()

            self._assertFilesRestored()

    def testCreateNetXmlBridged(self):
        expectedDoc = """<network>
                           <name>vdsm-awesome_net</name>
                           <forward mode='bridge'/>
                           <bridge name='awesome_net'/>
                         </network>"""
        actualDoc = libvirt.createNetworkDef('awesome_net', bridged=True)

        self.assertEqualXml(expectedDoc, actualDoc)

    def testCreateNetXml(self):
        iface = "dummy"
        expectedDoc = ("""<network>
                            <name>vdsm-awesome_net</name>
                            <forward mode='passthrough'>
                            <interface dev='%s'/>
                            </forward>
                          </network>""" % iface)
        actualDoc = libvirt.createNetworkDef('awesome_net', bridged=False,
                                             iface=iface)

        self.assertEqualXml(expectedDoc, actualDoc)
