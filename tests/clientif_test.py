#
# Copyright 2014-2019 Red Hat, Inc.
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

from __future__ import absolute_import
from __future__ import division

import collections
import json
import libvirt
import logging
import os.path
import threading
import xml.etree.ElementTree as etree

from unittest import mock

from vdsm import clientIF
from vdsm.common import libvirtconnection, response
from vdsm.virt import recovery
from vdsm.virt.vm import VolumeError

from testlib import VdsmTestCase as TestCaseBase
from testlib import temporaryPath
from monkeypatch import MonkeyPatch, MonkeyPatchScope

import vmfakecon
import fakelib

from virt import vmfakelib as fake


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
        self.vm_container_lock = threading.Lock()
        self.vmContainer = {}
        self.vmRequests = {}
        self.servers = {}
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

    def test_lun_drive_not_visible(self):
        def _not_visible(self, guid):
            return {
                "visible": {
                    guid: False,
                },
            }
        self.cif.irs.getDeviceVisibility = _not_visible
        self.assertRaisesRegexp(
            VolumeError,
            'Drive ^[a-z]*$ not visible')

    def test_lun_drive_not_appropriatable(self):
        def _not_appropriatable(self, guid, vmid):
            # any error is actually fine
            return response.error('unexpected')
        self.cif.irs.appropriateDevice = _not_appropriatable
        self.assertRaisesRegexp(
            VolumeError,
            'Cannot appropriate drive ^[a-z]*$')

    @MonkeyPatch(clientIF, 'supervdsm', FakeSuperVdsm())
    def testSuperVdsmFailure(self):
        def fail(*args, **kwargs):
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
        self.cif.servers["jsonrpc"] = self.serv

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


class TestPrepareNetworkDrive(TestCaseBase):

    def test_path_replacement(self):
        volinfo = {
            "path": "v/sd/images/img/vol_id",
            "protocol": "gluster",
            "hosts": ["host_one", "host_two"]
        }
        res = {"info": volinfo}

        volume_chain = [
            {"volumeID": "11111111-1111-1111-1111-111111111111"},
            {"volumeID": "22222222-2222-2222-2222-222222222222"}
        ]
        drive = fakeDrive()
        drive['volumeChain'] = volume_chain

        clientIF = FakeClientIF()
        actual = clientIF._prepare_network_drive(drive, res)

        expected = volinfo['path']
        expected_chain = [
            {
                "volumeID": "11111111-1111-1111-1111-111111111111",
                "path": "v/sd/images/img/11111111-1111-1111-1111-111111111111"
            },
            {
                "volumeID": "22222222-2222-2222-2222-222222222222",
                "path": "v/sd/images/img/22222222-2222-2222-2222-222222222222"
            }
        ]

        self.assertEqual(actual, expected)
        self.assertEqual(drive['protocol'], 'gluster')
        self.assertEqual(drive['hosts'], ['host_one'])
        self.assertEqual(drive['volumeChain'], expected_chain)


class FakeConnection(vmfakecon.Connection):

    def __init__(self, known_uuids, error=libvirt.VIR_ERR_NO_DOMAIN):
        self.known_uuids = known_uuids
        self.error = error

    def lookupByUUIDString(self, uuid):
        if uuid in self.known_uuids:
            return vmfakecon.FakeRunningVm(uuid)
        else:
            error = libvirt.libvirtError("error")
            error.err = [self.error]
            raise error


class NotSoFakeClientIF(clientIF.clientIF):

    def __init__(self):
        irs = None
        log = logging.getLogger('test.ClientIF')
        scheduler = None
        fake_start = mock.Mock()
        with MonkeyPatchScope([
                (clientIF, '_glusterEnabled', False),
                (clientIF, 'secret', {}),
                (clientIF, 'MomClient', lambda *args: mock.Mock()),
                (clientIF, 'QemuGuestAgentPoller', lambda *args: fake_start),
                (clientIF, 'Listener', lambda *args: mock.Mock()),
                (clientIF.concurrent, 'thread',
                 lambda *args, **kwargs: fake_start),
        ]):
            super(NotSoFakeClientIF, self).__init__(irs, log, scheduler)

    def _createAcceptor(self, host, port):
        pass

    def _prepareHttpServer(self):
        pass

    def _prepareJSONRPCServer(self):
        pass

    def _connectToBroker(self):
        pass


class FakeVm(object):

    def __init__(self, cif, vmParams, vmRecover=False):
        dom = etree.fromstring(vmParams['xml'])
        self.id = dom.find('.//uuid').text

    def run(self):
        return response.success()


class TestExternalVMTracking(TestCaseBase):

    def setUp(self):
        self.cif = NotSoFakeClientIF()
        self.dom_class = collections.namedtuple('Dom', 'UUIDString')
        self._dispatch_events([
            ('1', libvirt.VIR_DOMAIN_EVENT_DEFINED),
            ('2', libvirt.VIR_DOMAIN_EVENT_DEFINED),
            ('2', libvirt.VIR_DOMAIN_EVENT_DEFINED),
            ('3', libvirt.VIR_DOMAIN_EVENT_UNDEFINED),
            ('1', libvirt.VIR_DOMAIN_EVENT_STARTED),
        ])

    def _dispatch_events(self, vm_events):
        for vm_id, event in vm_events:
            dom = self.dom_class(UUIDString=lambda: vm_id)
            self.cif.dispatchLibvirtEvents(None, dom, event, 0, 0)

    def test_lookup_unknown_vm(self):
        vmid = '0000'
        dom = self.dom_class(UUIDString=lambda: vmid)
        self.assertEqual(self.cif.getVMs(), {})
        eventid, v = self.cif.lookup_vm_from_event(
            dom, libvirt.VIR_DOMAIN_EVENT_ID_REBOOT, 0, 0)
        self.assertIs(v, None)
        self.assertNotIn(vmid, self.cif.pop_unknown_vm_ids())

    def test_dispatch_unknown_vm(self):
        cif = NotSoFakeClientIF()
        cif.log = fakelib.FakeLogger()

        dom = self.dom_class(UUIDString=lambda: '0000')
        self.assertEqual(cif.getVMs(), {})

        cif.dispatchLibvirtEvents(
            None, dom, 0, 0, libvirt.VIR_DOMAIN_EVENT_ID_LIFECYCLE)

        for level, fmt, args in cif.log.messages:
            self.assertNotEqual(level, logging.ERROR)

    def test_external_vms_lookup(self):
        self.assertEqual(sorted(self.cif.pop_unknown_vm_ids()),
                         ['1', '2'])
        self.assertEqual(self.cif.pop_unknown_vm_ids(), [])

    @MonkeyPatch(libvirtconnection, 'get', lambda: FakeConnection(['2', '3']))
    def test_external_vm_ids_removal(self):
        with MonkeyPatchScope([
                (clientIF, 'Vm', FakeVm)
        ]):
            recovery.lookup_external_vms(self.cif)
        self.assertEqual(sorted(self.cif.pop_unknown_vm_ids()), [])
        self.assertEqual(sorted(self.cif.vmContainer.keys()), ['2'])

    @MonkeyPatch(
        libvirtconnection, 'get',
        lambda: FakeConnection(['2', '3'], error=libvirt.VIR_ERR_ERROR)
    )
    def test_external_vm_ids_errors(self):
        with MonkeyPatchScope([
                (clientIF, 'Vm', None)
        ]):
            recovery.lookup_external_vms(self.cif)
        self.assertEqual(sorted(self.cif.pop_unknown_vm_ids()), ['1'])
        self.assertEqual(sorted(self.cif.vmContainer.keys()), [])

    @MonkeyPatch(libvirtconnection, 'get', lambda: FakeConnection(['2', '3']))
    def test_external_vm_recovery_errors(self):
        with MonkeyPatchScope([
                (clientIF, 'Vm', FakeVm)
        ]):
            recovery.lookup_external_vms(self.cif)
        self.assertEqual(sorted(self.cif.pop_unknown_vm_ids()), [])
        self.assertEqual(sorted(self.cif.vmContainer.keys()), ['2'])
