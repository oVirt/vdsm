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

import logging
import os.path
from testrunner import VdsmTestCase as TestCaseBase
from testrunner import temporaryPath
from monkeypatch import MonkeyPatch
from virt.vm import VolumeError
import clientIF


INEXISTENT_PATH = '/no/such/path'
FAKE_ISOFS_PATH = '/fake/path/to/isofs'
FAKE_FLOPPY_PATH = '/fake/path/to/floppy'
ISOFS_PATH = '/rhev/data-center/mnt/A.B.C.D:_ovirt_iso/XXX' \
             '/images/11111111-1111-1111-1111-111111111111/' \
             'Fedora-Live-Desktop-x86_64-19.iso'


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


class FakeClientIF(clientIF.clientIF):
    def __init__(self):
        # the bare minimum initialization for our test needs.
        self.irs = None  # just to make sure nothing ever happens
        self.log = logging.getLogger('ClientIFTests')


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
        self.assertEquals(name, funcName)

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
            self.assertEquals(volPath, f)

    def testBadDrive(self):
        assert not os.path.exists(INEXISTENT_PATH)
        self.assertRaises(VolumeError,
                          self.cif.prepareVolumePath,
                          INEXISTENT_PATH)

    @MonkeyPatch(clientIF, 'supervdsm', FakeSuperVdsm())
    def testCDRomFromPayload(self):
        # bz1047356
        volPath = self.cif.prepareVolumePath(fakePayloadDrive())
        self.assertEquals(volPath, FAKE_ISOFS_PATH)
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
        self.assertEquals(volPath, FAKE_ISOFS_PATH)
        self.assertCalled('mkIsoFs')

    @MonkeyPatch(clientIF, 'supervdsm', FakeSuperVdsm())
    def testDriveWithoutSpecParams(self):
        drive = fakeDrive()
        del drive['specParams']
        volPath = self.cif.prepareVolumePath(drive)
        self.assertEquals(volPath, ISOFS_PATH)
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
        self.assertEquals(volPath, '')
        # real drive, but not iso image attached.
        self.assertNotCalled('mkIsoFs')

    @MonkeyPatch(clientIF, 'supervdsm', FakeSuperVdsm())
    def testCDromPath(self):
        volPath = self.cif.prepareVolumePath(fakeDrive())
        self.assertEquals(volPath, ISOFS_PATH)
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
        self.assertEquals(volPath, ISOFS_PATH)

    @MonkeyPatch(clientIF, 'supervdsm', FakeSuperVdsm())
    def testSuperVdsmFailure(self):
        def fail(*args):
            raise RuntimeError('Injected fail')
        sv = clientIF.supervdsm.getProxy()
        sv.mkIsoFs = fail
        self.assertRaises(RuntimeError,
                          self.cif.prepareVolumePath,
                          fakePayloadDrive())
