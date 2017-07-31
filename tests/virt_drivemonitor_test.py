#
# Copyright 2017 Red Hat, Inc.
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

from contextlib import contextmanager
import logging
import threading

import libvirt

from vdsm.common import response
from vdsm.virt.vmdevices.storage import Drive, DISK_TYPE
from vdsm.virt.vmdevices import hwclass
from vdsm.virt import vm
from vdsm.virt import vmstatus

from testlib import VdsmTestCase
import vmfakelib as fake

from monkeypatch import MonkeyPatchScope


MB = 1024 ** 2
GB = 1024 ** 3


CHUNK_SIZE_GB = 1 * GB
CHUNK_PCT = 50


@contextmanager
def make_env():
    log = logging.getLogger('test')

    # the Drive class use those two tunables as class constants.
    with MonkeyPatchScope([
        (Drive, 'VOLWM_CHUNK_SIZE', CHUNK_SIZE_GB),
        (Drive, 'VOLWM_FREE_PCT', CHUNK_PCT),
    ]):
        # storage does not validate the UUIDs, so we use phony names
        # for brevity
        drives = [
            Drive(log, **drive_config(
                format='cow',
                diskType=DISK_TYPE.BLOCK,
                index=0,
                volumeID='volume_0',
                poolID='pool_0',
                imageID='image_0',
                domainID='domain_0',

            )),
            Drive(log, **drive_config(
                format='cow',
                diskType=DISK_TYPE.BLOCK,
                index=1,
                volumeID='volume_1',
                poolID='pool_0',
                imageID='image_1',
                domainID='domain_0',
            )),
        ]
        # TODO: add raw/block drive and qcow2/file drive.
        # check we don't try to monitor or extend those drives.

        cif = FakeClientIF()
        cif.irs = FakeIRS()
        dom = FakeDomain()
        yield FakeVM(cif, dom, drives), dom, drives


def allocation_threshold_for_resize_mb(block_info, drive):
    return block_info['physical'] - drive.watermarkLimit


class DiskExtensionTests(VdsmTestCase):

    def test_no_extension_allocation_below_watermark(self):

        with make_env() as (testvm, dom, drives):
            vda = dom.block_info['/virtio/0']
            vda['allocation'] = 0 * MB
            vdb = dom.block_info['/virtio/1']
            vdb['allocation'] = allocation_threshold_for_resize_mb(
                vdb, drives[1]) - 1 * MB

            extended = testvm.monitor_drives()

        self.assertEqual(extended, False)

    def test_no_extension_maximum_size_reached(self):

        with make_env() as (testvm, dom, drives):
            vda = dom.block_info['/virtio/0']
            vda['allocation'] = 0 * MB
            vdb = dom.block_info['/virtio/1']
            max_size = drives[1].getMaxVolumeSize(vdb['capacity'])
            vdb['allocation'] = max_size
            vdb['physical'] = max_size
            extended = testvm.monitor_drives()

        self.assertEqual(extended, False)

    def test_extend_drive_allocation_crosses_watermark_limit(self):

        with make_env() as (testvm, dom, drives):
            vda = dom.block_info['/virtio/0']
            vda['allocation'] = 0 * MB
            vdb = dom.block_info['/virtio/1']
            vdb['allocation'] = allocation_threshold_for_resize_mb(
                vdb, drives[1]) + 1 * MB

            extended = testvm.monitor_drives()

        self.assertEqual(extended, True)
        self.assertEqual(len(testvm.cif.irs.extensions), 1)
        self.check_extension(vdb, drives[1], testvm.cif.irs.extensions[0])

    def test_extend_drive_allocation_equals_next_size(self):

        with make_env() as (testvm, dom, drives):
            vda = dom.block_info['/virtio/0']
            vda['allocation'] = drives[0].getNextVolumeSize(
                vda['physical'], vda['capacity'])
            vdb = dom.block_info['/virtio/1']
            vdb['allocation'] = 0 * MB
            extended = testvm.monitor_drives()

        self.assertEqual(extended, True)
        self.assertEqual(len(testvm.cif.irs.extensions), 1)
        self.check_extension(vda, drives[0], testvm.cif.irs.extensions[0])

    def test_stop_extension_loop_on_improbable_request(self):

        with make_env() as (testvm, dom, drives):
            vda = dom.block_info['/virtio/0']
            vda['allocation'] = (
                drives[0].getNextVolumeSize(
                    vda['physical'], vda['capacity']) + 1 * MB)
            vdb = dom.block_info['/virtio/1']
            vdb['allocation'] = 0 * MB
            extended = testvm.monitor_drives()

        self.assertEqual(extended, False)
        self.assertEqual(dom.info()[0], libvirt.VIR_DOMAIN_PAUSED)

    # TODO: add the same test for disk replicas.
    def test_vm_resumed_after_drive_extended(self):

        with make_env() as (testvm, dom, drives):
            testvm.pause()

            vda = dom.block_info['/virtio/0']
            vda['allocation'] = 0 * MB
            vdb = dom.block_info['/virtio/1']  # shortcut
            vdb['allocation'] = allocation_threshold_for_resize_mb(
                vdb, drives[1]) + 1 * MB

            extended = testvm.monitor_drives()
            self.assertEqual(extended, True)
            self.assertEqual(len(testvm.cif.irs.extensions), 1)

            # Simulate completed extend operation, invoking callback

            poolID, volInfo, newSize, func = testvm.cif.irs.extensions[0]
            key = (volInfo['domainID'], volInfo['poolID'],
                   volInfo['imageID'], volInfo['volumeID'])
            # Simulate refresh, updating local volume size
            testvm.cif.irs.volume_sizes[key] = newSize

            func(volInfo)

            # Calling refreshVolume is critical in this flow.
            # Check this indeed happened.
            self.assertEqual(key, testvm.cif.irs.refreshes[0])

        self.assertEqual(testvm.lastStatus, vmstatus.UP)
        self.assertEqual(dom.info()[0], libvirt.VIR_DOMAIN_RUNNING)

    # TODO: add test with storage failures in the extension flow

    # helpers

    def check_extension(self, drive_info, drive_obj, extension_req):
        poolID, volInfo, newSize, func = extension_req

        # we do the minimal validation. Specific test(s) should
        # check that the callable actually finishes the extension process.
        self.assertTrue(callable(func))

        self.assertEqual(drive_obj.poolID, poolID)

        expected_size = drive_obj.getNextVolumeSize(
            drive_info['physical'], drive_info['capacity'])
        self.assertEqual(expected_size, newSize)

        self.assertEqual(expected_size, volInfo['newSize'])
        self.assertEqual(drive_obj.name, volInfo['name'])

        if drive_obj.isDiskReplicationInProgress():
            self.assertEqual(drive_obj.diskReplicate['domainID'],
                             volInfo['domainID'])
            self.assertEqual(drive_obj.diskReplicate['imageID'],
                             volInfo['imageID'])
            self.assertEqual(drive_obj.diskReplicate['poolID'],
                             volInfo['poolID'])
            self.assertEqual(drive_obj.diskReplicate['volumeID'],
                             volInfo['volumeID'])
        else:
            self.assertEqual(drive_obj.domainID, volInfo['domainID'])
            self.assertEqual(drive_obj.imageID, volInfo['imageID']),
            self.assertEqual(drive_obj.poolID, volInfo['poolID']),
            self.assertEqual(drive_obj.volumeID, volInfo['volumeID'])


class FakeVM(vm.Vm):
    def __init__(self, cif, dom, disks):
        self.id = 'drive_monitor_vm'
        self.cif = cif
        self._dom = dom
        self._devices = {hwclass.DISK: disks}

        # needed for pause()/cont()

        self._lastStatus = vmstatus.UP
        self._guestCpuRunning = True
        self._custom = {}
        self._confLock = threading.Lock()
        self.conf = {}
        self._guestCpuLock = threading.Lock()

    # to reduce the amount of faking needed, we fake those methods
    # which are not relevant to the monitor_drives() flow

    def send_status_event(self, **kwargs):
        pass

    def isMigrating(self):
        return False


class FakeDomain(object):

    def __init__(self):
        self._state = (libvirt.VIR_DOMAIN_RUNNING, )
        self.block_info = {
            # capacity is random value > 0
            # physical is random value > 0, <= capacity
            '/virtio/0': {
                'capacity': 4 * GB,
                'allocation': 0 * GB,
                'physical': 2 * GB,
            },
            '/virtio/1': {
                'capacity': 2 * GB,
                'allocation': 0 * GB,
                'physical': 1 * GB,
            },
        }

    def blockInfo(self, path, flags):
        # TODO: support access by name
        # flags is ignored
        d = self.block_info[path]
        return d['capacity'], d['allocation'], d['physical']

    # The following is needed in the 'pause' flow triggered
    # by the ImprobableResizeRequestError

    def XMLDesc(self, flags):
        return u'<domain/>'

    def suspend(self):
        self._state = (libvirt.VIR_DOMAIN_PAUSED, )

    def resume(self):
        self._state = (libvirt.VIR_DOMAIN_RUNNING, )

    def info(self):
        return self._state


class FakeClientIF(fake.ClientIF):

    def notify(self, event_id, params=None):
        pass


class FakeIRS(object):

    def __init__(self):
        self.extensions = []
        self.refreshes = []
        self.volume_sizes = {}

    def sendExtendMsg(self, poolID, volInfo, newSize, func):
        self.extensions.append((poolID, volInfo, newSize, func))

    def refreshVolume(self, domainID, poolID, imageID, volumeID):
        key = (domainID, poolID, imageID, volumeID)
        self.refreshes.append(key)

    def getVolumeSize(self, domainID, poolID, imageID, volumeID):
        # For block storage we "truesize" and "apparentsize" are always
        # the same, they exists only for compatibility with file volumes
        key = (domainID, poolID, imageID, volumeID)
        size = self.volume_sizes[key]
        return response.success(apparentsize=size, truesize=size)


# TODO: factor out this function and its counterpart in vmstorage_test.py
def drive_config(**kw):
    ''' Return drive configuration updated from **kw '''
    conf = {
        'device': 'disk',
        'format': 'raw',
        'iface': 'virtio',
        'index': '0',
        'propagateErrors': 'off',
        'readonly': 'False',
        'shared': 'none',
        'type': 'disk',
    }
    conf.update(kw)
    conf['path'] = '/{iface}/{index}'.format(
        iface=conf['iface'], index=conf['index']
    )
    return conf
