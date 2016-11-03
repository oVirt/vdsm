#
# Copyright 2012 IBM, Inc.
# Copyright 2012-2016 Red Hat, Inc.
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
import os
import pwd
import re
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET

from vdsm.network import libvirt
from vdsm.network.configurators import ifcfg

from monkeypatch import MonkeyPatch
from monkeypatch import MonkeyPatchScope
from nose.plugins.attrib import attr
from nose.plugins.skip import SkipTest
from testlib import VdsmTestCase as TestCaseBase, mock


@attr(type='unit')
class ifcfgConfigWriterTests(TestCaseBase):
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

    def assertEqualXml(self, a, b, msg=None):
        """
        Compare two xml strings for equality.
        """

        aXml = ET.tostring(ET.fromstring(a))
        bXml = ET.tostring(ET.fromstring(b))

        aXmlNrml = re.sub('>\s*\n\s*<', '><', aXml).strip()
        bXmlNrml = re.sub('>\s*\n\s*<', '><', bXml).strip()

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

    @attr(type='integration')
    @MonkeyPatch(os, 'chown', lambda *x: 0)
    def testPersistentBackup(self):

        with MonkeyPatchScope([
            (ifcfg, 'NET_CONF_BACK_DIR',
             os.path.join(self._tempdir, 'netback')),
            (ifcfg, 'NET_CONF_DIR', self._tempdir),
            (ifcfg, 'NET_CONF_PREF',
             os.path.join(self._tempdir, 'ifcfg-')),
            (ifcfg, 'ifdown', lambda x: 0),
            (ifcfg, '_exec_ifup', lambda *x: 0),
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


IFCFG_ETH_CONF = """DEVICE="testdevice"
ONBOOT=yes
NETBOOT=yes
UUID="237dcf6d-516d-4a85-8651-f81e2f4a6238"
IPV6INIT=yes
BOOTPROTO=dhcp
TYPE=Ethernet
NAME="enp0s25"
DEFROUTE=yes
IPV4_FAILURE_FATAL=no
IPV6_AUTOCONF=yes
IPV6_DEFROUTE=yes
IPV6_FAILURE_FATAL=no
HWADDR=68:F7:28:C3:CE:E5
PEERDNS=yes
PEERROUTES=yes
IPV6_PEERDNS=yes
IPV6_PEERROUTES=yes
"""

IFCFG_VLAN_CONF = """VLAN=yes
TYPE=Vlan
PHYSDEV=testdevice
VLAN_ID=100
REORDER_HDR=0
BOOTPROTO=none
IPADDR=19.19.19.19
PREFIX=29
DEFROUTE=yes
IPV4_FAILURE_FATAL=no
IPV6INIT=yes
IPV6_AUTOCONF=yes
IPV6_DEFROUTE=yes
IPV6_PEERDNS=yes
IPV6_PEERROUTES=yes
IPV6_FAILURE_FATAL=no
NAME=vlan
UUID=95d45ecb-99f8-46cd-8942-a34cb5c1e321
ONBOOT=yes

"""


@attr(type='unit')
@mock.patch.object(ifcfg.utils, 'rmFile')
@mock.patch.object(ifcfg.os, 'rename')
@mock.patch.object(ifcfg.glob, 'iglob')
@mock.patch.object(ifcfg, 'open', create=True)
class IfcfgAcquireTests(TestCaseBase):

    def test_acquire_iface_given_non_standard_filename(self,
                                                       mock_open,
                                                       mock_list_files,
                                                       mock_rename,
                                                       mock_rmfile):
        ifcfg.open.return_value.__enter__.return_value = IFCFG_ETH_CONF.split()
        mock_list_files.return_value = ['filename1']

        ifcfg.IfcfgAcquire.acquire_device('testdevice')

        mock_rename.assert_called_once_with(
            'filename1', ifcfg.NET_CONF_PREF + 'testdevice')

    def test_acquire_iface_given_multiple_files_for_the_iface(self,
                                                              mock_open,
                                                              mock_list_files,
                                                              mock_rename,
                                                              mock_rmfile):
        ifcfg.open.return_value.__enter__.return_value = IFCFG_ETH_CONF.split()
        mock_list_files.return_value = ['filename1', 'filename2']

        ifcfg.IfcfgAcquire.acquire_device('testdevice')

        mock_rename.assert_called_once_with(
            'filename1', ifcfg.NET_CONF_PREF + 'testdevice')
        mock_rmfile.assert_called_once_with('filename2')

    def test_acquire_vlan_iface_given_nm_unique_config(self,
                                                       mock_open,
                                                       mock_list_files,
                                                       mock_rename,
                                                       mock_rmfile):
        conf_list = IFCFG_VLAN_CONF.split()
        ifcfg.open.return_value.__enter__.return_value = conf_list
        mock_list_files.return_value = ['filename1', 'filename2']

        ifcfg.IfcfgAcquire.acquire_vlan_device('testdevice.100')

        mock_rename.assert_called_once_with(
            'filename1', ifcfg.NET_CONF_PREF + 'testdevice.100')
        mock_rmfile.assert_called_once_with('filename2')
