#
# Copyright 2014 Red Hat, Inc.
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

import json
import logging
import os.path
import threading

from vdsm.common import response
from virt.vm import VolumeError

from testlib import VdsmTestCase as TestCaseBase
from testlib import temporaryPath
from monkeypatch import MonkeyPatch

import clientIF

import vmfakelib as fake


INEXISTENT_PATH = '/no/such/path'
FAKE_ISOFS_PATH = '/fake/path/to/isofs'
FAKE_FLOPPY_PATH = '/fake/path/to/floppy'
ISOFS_PATH = '/rhev/data-center/mnt/A.B.C.D:_ovirt_iso/XXX' \
             '/images/11111111-1111-1111-1111-111111111111/' \
             'Fedora-Live-Desktop-x86_64-19.iso'


class FakeClientIF(clientIF.clientIF):
    def __init__(self):
        # the bare minimum initialization for our test needs.
        self.irs = fake.IRS()  # just to make sure nothing ever happens
        self.log = logging.getLogger('fake.ClientIF')
        self.channelListener = None
        self.vmContainerLock = threading.Lock()
        self.vmContainer = {}
        self.vmRequests = {}
        self.bindings = {}
        self._recovery = False

    def createVm(self, vmParams, vmRecover=False):
        self.vmRequests[vmParams['vmId']] = (vmParams, vmRecover)
        return response.success(vmList={})


class FakeSuperVdsm:
    def __init__(self):
        self.calls = []

    def getProxy(self):
        return self

    def mkIsoFs(self, *args, **kwargs):
        self.calls.append(('mkIsoFs', args, kwargs))
        return FAKE_ISOFS_PATH

    def mkFloppyFs(self, *args, **kwargs):
        self.calls.append(('mkFloppyFs', args, kwargs))
        return FAKE_FLOPPY_PATH


def fakeDrive():
    return {
        'index': '2',
        'iface': 'ide',
        'specParams': {},
        'readonly': 'true',
        'deviceId': 'XXX',
        'path': ISOFS_PATH,
        'device': 'cdrom',
        'shared': 'false',
        'type': 'disk'
    }


def fakePayloadDrive():
    drive = fakeDrive()
    drive['path'] = ''  # took from VDSM logs
    drive['specParams'] = {
        'vmPayload': {
            'volId': 'config-2',
            'file': {
                'openstack/latest/meta_data.json': '',
                'openstack/latest/user_data': '',
            }
        }
    }
    return drive


class ClientIFTests(TestCaseBase):

    def setUp(self):
        self.cif = FakeClientIF()

    def assertCalled(self, funcName):
        sv = clientIF.supervdsm.getProxy()
        name, args, kwargs = sv.calls[0]
        self.assertEqual(name, funcName)

    def assertNotCalled(self, funcName):
        sv = clientIF.supervdsm.getProxy()
        for name, args, kwargs in sv.calls:
            if name == funcName:
                raise self.failureException('%s was called' % (funcName))

    def testNoneDrive(self):
        # extreme case. Should never happen.
        volPath = self.cif.prepareVolumePath(None)
        self.assertTrue(volPath is None)

    def testStringAsDrive(self):
        with temporaryPath() as f:
            volPath = self.cif.prepareVolumePath(f)
            self.assertEqual(volPath, f)

    def testBadDrive(self):
        assert not os.path.exists(INEXISTENT_PATH)
        self.assertRaises(VolumeError,
                          self.cif.prepareVolumePath,
                          INEXISTENT_PATH)

    @MonkeyPatch(clientIF, 'supervdsm', FakeSuperVdsm())
    def testCDRomFromPayload(self):
        # bz1047356
        volPath = self.cif.prepareVolumePath(fakePayloadDrive())
        self.assertEqual(volPath, FAKE_ISOFS_PATH)
        self.assertCalled('mkIsoFs')

    @MonkeyPatch(clientIF, 'supervdsm', FakeSuperVdsm())
    def testNoPayloadFileKey(self):
        payloadDrive = fakePayloadDrive()
        del payloadDrive['specParams']['vmPayload']['file']
        self.assertRaises(KeyError,
                          self.cif.prepareVolumePath,
                          payloadDrive)

    @MonkeyPatch(clientIF, 'supervdsm', FakeSuperVdsm())
    def testNoPayloadVolIdKey(self):
        payloadDrive = fakePayloadDrive()
        del payloadDrive['specParams']['vmPayload']['volId']
        volPath = self.cif.prepareVolumePath(payloadDrive)
        self.assertEqual(volPath, FAKE_ISOFS_PATH)
        self.assertCalled('mkIsoFs')

    @MonkeyPatch(clientIF, 'supervdsm', FakeSuperVdsm())
    def testDriveWithoutSpecParams(self):
        drive = fakeDrive()
        del drive['specParams']
        volPath = self.cif.prepareVolumePath(drive)
        self.assertEqual(volPath, ISOFS_PATH)
        # this is a fallback case explicitely marked
        # as 'for Backward Compatibility sake'
        # in the code
        self.assertNotCalled('mkIsoFs')

    def testDriveWithoutSpecParamsAndPath(self):
        drive = fakeDrive()
        del drive['specParams']
        del drive['path']
        self.assertRaises(VolumeError,
                          self.cif.prepareVolumePath,
                          drive)

    @MonkeyPatch(clientIF, 'supervdsm', FakeSuperVdsm())
    def testCDRomEmpty(self):
        drive = fakeDrive()
        drive['specParams']['path'] = ''
        drive['path'] = ''
        volPath = self.cif.prepareVolumePath(drive)
        self.assertEqual(volPath, '')
        # real drive, but not iso image attached.
        self.assertNotCalled('mkIsoFs')

    @MonkeyPatch(clientIF, 'supervdsm', FakeSuperVdsm())
    def testCDromPath(self):
        volPath = self.cif.prepareVolumePath(fakeDrive())
        self.assertEqual(volPath, ISOFS_PATH)
        # mkIsoFs should be called only to generate images
        # on the flight if payload is given (cloud-init)
        self.assertNotCalled('mkIsoFs')

    def testDriveWithoutDeviceKey(self):
        drive = fakeDrive()
        del drive['device']
        self.assertRaises(KeyError,
                          self.cif.prepareVolumePath,
                          drive)

    def testDriveWithUnsupportedDeviceKey(self):
        drive = fakeDrive()
        drive['device'] = 'tape'
        # fallback case: we must use the top-level key 'path'
        volPath = self.cif.prepareVolumePath(drive)
        self.assertEqual(volPath, ISOFS_PATH)

    @MonkeyPatch(clientIF, 'supervdsm', FakeSuperVdsm())
    def testSuperVdsmFailure(self):
        def fail(*args):
            raise RuntimeError('Injected fail')
        sv = clientIF.supervdsm.getProxy()
        sv.mkIsoFs = fail
        self.assertRaises(RuntimeError,
                          self.cif.prepareVolumePath,
                          fakePayloadDrive())


class getVMsTests(TestCaseBase):

    def test_empty(self):
        cif = FakeClientIF()
        self.assertFalse(cif.getVMs())

    def test_with_vms(self):
        cif = FakeClientIF()
        with fake.VM(params={'vmId': 'testvm1'}, cif=cif) as testvm1:
            with fake.VM(params={'vmId': 'testvm2'}, cif=cif) as testvm2:
                vms = cif.getVMs()
                self.assertEqual(len(vms), 2)
                self.assertIn(testvm1.id, vms)
                self.assertIn(testvm2.id, vms)


class TestNotification(TestCaseBase):

    TEST_EVENT_NAME = 'test_event'

    def setUp(self):
        self.cif = FakeClientIF()
        self.serv = fake.JsonRpcServer()
        self.cif.bindings["jsonrpc"] = self.serv

    def test_notify(self):
        self.assertTrue(self.cif.ready)
        self.cif.notify(self.TEST_EVENT_NAME)
        message, address = self.serv.notifications[0]
        self._assertEvent(message, self.TEST_EVENT_NAME)

    def test_skip_notify_in_recovery(self):
        self.cif._recovery = True
        self.assertFalse(self.cif.ready)
        self.cif.notify('test_event')
        self.assertEqual(self.serv.notifications, [])

    def _assertEvent(self, event, method):
        ev = json.loads(event)
        self.assertEqual(ev["method"], method)
