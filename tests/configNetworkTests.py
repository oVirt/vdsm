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
import re
import subprocess
import tempfile
import shutil
import pwd
from contextlib import contextmanager
import inspect

import configNetwork
from vdsm import netinfo
from vdsm.utils import memoized

from testrunner import VdsmTestCase as TestCaseBase
from nose.plugins.skip import SkipTest


class TestconfigNetwork(TestCaseBase):
    @contextmanager
    def _raisesContextManager(self, excName):
        try:
            yield self._raisesContextManager
        except excName as exception:
            self._raisesContextManager.__func__.exception = exception
        except:
            raise self.failureException, "%s not raised" % excName
        else:
            raise self.failureException, "%s not raised" % excName

    def _assertRaises(self, excName, callableObj=None, *args, **kwargs):
        if callableObj is None:
            return self._raisesContextManager(excName)
        else:
            with self._raisesContextManager(excName):
                callableObj(*args, **kwargs)

    def setUp(self):
        # When assertRaises does not have a default argument it does not
        # support being used ad context manager. Thus, we redefine it.
        if inspect.getargspec(self.assertRaises)[3] is None:
            self.assertRaises = self._assertRaises

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

    def testIsVlanIdValid(self):
        vlanIds = ('badValue', configNetwork.MAX_VLAN_ID + 1)

        for vlanId in vlanIds:
            with self.assertRaises(configNetwork.ConfigNetworkError) \
                    as cneContext:
                configNetwork.validateVlanId(vlanId)
            self.assertEqual(cneContext.exception.errCode,
                             configNetwork.ne.ERR_BAD_VLAN)

        self.assertEqual(configNetwork.validateVlanId(0), None)
        self.assertEqual(configNetwork.validateVlanId(configNetwork.
                                                      MAX_VLAN_ID),
                         None)

    def testIsBondingNameValid(self):
        bondNames = ('badValue', ' bond14', 'bond14 ', 'bond14a', 'bond0 0')

        for bondName in bondNames:
            with self.assertRaises(configNetwork.ConfigNetworkError) \
                    as cneContext:
                configNetwork.validateBondingName(bondName)
            self.assertEqual(cneContext.exception.errCode,
                             configNetwork.ne.ERR_BAD_BONDING)

        self.assertEqual(configNetwork.validateBondingName('bond11'), None)
        self.assertEqual(configNetwork.validateBondingName('bond11128421982'),
                         None)

    def testIsIpValid(self):
        addresses = ('10.18.1.254', '10.50.25.177', '250.0.0.1',
                     '20.20.25.25')
        badAddresses = ('192.168.1.256', '10.50.25.1777', '256.0.0.1',
                        '20.20.25.25.25')

        for address in badAddresses:
            with self.assertRaises(configNetwork.ConfigNetworkError) \
                    as cneContext:
                configNetwork.validateIpAddress(address)
            self.assertEqual(cneContext.exception.errCode,
                             configNetwork.ne.ERR_BAD_ADDR)

        for address in addresses:
            self.assertEqual(configNetwork.validateIpAddress(address), None)

    def testIsNetmaskValid(self):
        masks = ('10.18.1.254', '10.50.25.177', '250.0.0.1',
                 '20.20.25.25')
        badMasks = ('192.168.1.256', '10.50.25.1777', '256.0.0.1',
                    '20.20.25.25.25')

        for mask in badMasks:
            with self.assertRaises(configNetwork.ConfigNetworkError) \
                    as cneContext:
                configNetwork.validateNetmask(mask)
            self.assertEqual(cneContext.exception.errCode,
                             configNetwork.ne.ERR_BAD_ADDR)

        for mask in masks:
            self.assertEqual(configNetwork.validateNetmask(mask), None)

    @memoized
    def _bondingModuleOptions(self):
        p = subprocess.Popen(['modinfo', 'bonding'],
                             close_fds=True, stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        out, err = p.communicate()

        if err:
            return False

        return frozenset(re.findall(r'(\w+):', line)[1] for line in
                         out.split('\n') if line.startswith('parm:'))

    def _bondingOptExists(self, path):
        return os.path.basename(path) in self._bondingModuleOptions()

    def testValidateBondingOptions(self):
        # Monkey patch os.path.exists to let validateBondingOptions logic be
        # tested when a bonding device is not present.
        oldExists = os.path.exists
        os.path.exists = self._bondingOptExists

        opts = 'mode=802.3ad miimon=150'
        badOpts = 'foo=bar badopt=one'

        with self.assertRaises(configNetwork.ConfigNetworkError) as cne:
            configNetwork.validateBondingOptions('bond0', badOpts)
        self.assertEqual(cne.exception.errCode,
                         configNetwork.ne.ERR_BAD_BONDING)

        self.assertEqual(configNetwork.validateBondingOptions('bond0', opts),
                         None)

        # Restitute the stdlib's os.path.exists
        os.path.exists = oldExists

    def _fakeNetworks(self):
        return {
                'fakebridgenet': {'bridge': 'fakebridge', 'bridged': True},
                'fakenet': {'interface': 'fakeint', 'bridged': False},
               }

    def _addNetworkWithExc(self, parameters, errCode):
        with self.assertRaises(configNetwork.ConfigNetworkError) as cneContext:
            configNetwork._addNetworkValidation(*parameters)
        cne = cneContext.exception
        self.assertEqual(cne.errCode, errCode)

    def testAddNetworkValidation(self):
        # Monkey patch the real network detection from the netinfo module.
        oldValue = netinfo.networks
        netinfo.networks = self._fakeNetworks

        _netinfo = {
                'networks': {
                    'fakent': {'interface': 'fakeint', 'bridged': False},
                    'fakebrnet': {'bridge': 'fakebr', 'bridged': True, 'ports':
                        ['eth0', 'eth1']},
                    'fakebrnet1': {'bridge': 'fakebr1', 'bridged': True,
                        'ports': ['bond00']}
                    },
                'vlans': {
                    'fakevlan': {
                        'iface': 'eth3',
                        'addr': '10.10.10.10',
                        'netmask': '255.255.0.0',
                        'mtu': 1500
                        }
                    },
                'nics': ['eth0', 'eth1', 'eth2', 'eth3', 'eth4', 'eth5',
                         'eth6', 'eth7', 'eth8', 'eth9', 'eth10'],
                'bondings': {
                    'bond00': {
                        'slaves': ['eth5', 'eth6']
                        }
                    }
                }

        netinfoIns = netinfo.NetInfo(_netinfo)
        vlan = bonding = ipaddr = netmask = gw = bondingOptions = None
        nics = ['eth2']

        # Test for already existing bridge.
        self._addNetworkWithExc((netinfoIns, 'fakebrnet', vlan, bonding, nics,
                                 ipaddr, netmask, gw, bondingOptions),
                                configNetwork.ne.ERR_USED_BRIDGE)

        # Test for already existing network.
        self._addNetworkWithExc((netinfoIns, 'fakent', vlan, bonding, nics,
                                 ipaddr, netmask, gw, bondingOptions),
                                configNetwork.ne.ERR_USED_BRIDGE)

        # Test for bonding opts passed without bonding specified.
        self._addNetworkWithExc((netinfoIns, 'test', vlan, bonding, nics,
                                 ipaddr, netmask, gw, 'mode=802.3ad'),
                                configNetwork.ne.ERR_BAD_BONDING)

        # Test IP without netmask.
        self._addNetworkWithExc((netinfoIns, 'test', vlan, bonding, nics,
                                 '10.10.10.10', netmask, gw, bondingOptions),
                                configNetwork.ne.ERR_BAD_ADDR)

        #Test netmask without IP.
        self._addNetworkWithExc((netinfoIns, 'test', vlan, bonding, nics,
                                 ipaddr, '255.255.255.0', gw, bondingOptions),
                                configNetwork.ne.ERR_BAD_ADDR)

        #Test gateway without IP.
        self._addNetworkWithExc((netinfoIns, 'test', vlan, bonding, nics,
                                 ipaddr, netmask, '10.10.0.1', bondingOptions),
                                configNetwork.ne.ERR_BAD_ADDR)

        # Test for non existing nic.
        self._addNetworkWithExc((netinfoIns, 'test', vlan, bonding, ['eth11'],
                                 ipaddr, netmask, gw, bondingOptions),
                                configNetwork.ne.ERR_BAD_NIC)

        # Test for nic already bound to a different network.
        self._addNetworkWithExc((netinfoIns, 'test', vlan, 'bond0', ['eth0',
                                 'eth1'], ipaddr, netmask, gw, bondingOptions),
                                configNetwork.ne.ERR_USED_NIC)

        # Test for bond already member of a network.
        self._addNetworkWithExc((netinfoIns, 'test', vlan, 'bond00', ['eth5',
                                 'eth6'], ipaddr, netmask, gw, bondingOptions),
                                configNetwork.ne.ERR_BAD_PARAMS)

        # Test for multiple nics without bonding device.
        self._addNetworkWithExc((netinfoIns, 'test', vlan, bonding, ['eth3',
                                 'eth4'], ipaddr, netmask, gw, bondingOptions),
                                configNetwork.ne.ERR_BAD_BONDING)

        # Test for nic already in a bond.
        self._addNetworkWithExc((netinfoIns, 'test', vlan, bonding, ['eth6'],
                                 ipaddr, netmask, gw, bondingOptions),
                                configNetwork.ne.ERR_USED_NIC)

        # Restitute the real network detection of the netinfo module.
        netinfo.networks = oldValue


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
