#
# Copyright 2017-2020 Red Hat, Inc.
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

from contextlib import contextmanager
import logging
import threading

import libvirt

from vdsm import utils
from vdsm.common import response
from vdsm.common.units import MiB, GiB
from vdsm.virt.vmdevices.storage import Drive, DISK_TYPE, BLOCK_THRESHOLD
from vdsm.virt.vmdevices import hwclass
from vdsm.virt.utils import TimedAcquireLock
from vdsm.virt import drivemonitor
from vdsm.virt import vm
from vdsm.virt import vmstatus

from testlib import expandPermutations, permutations
from testlib import make_config
from testlib import maybefail
from testlib import VdsmTestCase

from . import vmfakelib as fake

from monkeypatch import MonkeyPatchScope


CHUNK_SIZE = 1 * GiB
CHUNK_PCT = 50

REPLICA_BASE_INDEX = 1000


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
    return conf


@contextmanager
def make_env(events_enabled, drive_infos):
    log = logging.getLogger('test')

    cfg = make_config([
        ('irs', 'enable_block_threshold_event',
            'true' if events_enabled else 'false')])

    # the Drive class use those two tunables as class constants.
    with MonkeyPatchScope([
        (Drive, 'VOLWM_CHUNK_SIZE', CHUNK_SIZE),
        (Drive, 'VOLWM_FREE_PCT', CHUNK_PCT),
        (drivemonitor, 'config', cfg),
    ]):
        dom = FakeDomain()
        irs = FakeIRS()

        drives = [
            make_drive(log, dom, irs, index, drive_conf, info)
            for index, (drive_conf, info) in enumerate(drive_infos)
        ]

        cif = FakeClientIF()
        cif.irs = irs
        yield FakeVM(cif, dom, drives), dom, drives


def allocation_threshold_for_resize_mb(block_info, drive):
    return block_info['physical'] - drive.watermarkLimit


class DiskExtensionTestBase(VdsmTestCase):
    # helpers

    BLOCK_INFOS = {
        'capacity': 4 * GiB, 'allocation': 1 * GiB, 'physical': 2 * GiB
    }

    DRIVE_INFOS = (
        (drive_config(format='cow', diskType=DISK_TYPE.BLOCK), BLOCK_INFOS),
        (drive_config(format='cow', diskType=DISK_TYPE.BLOCK), BLOCK_INFOS)
    )

    def check_extension(self, drive_info, drive_obj, extension_req):
        poolID, volInfo, newSize, func = extension_req

        # we do the minimal validation. Specific test(s) should
        # check that the callable actually finishes the extension process.
        assert callable(func)

        assert drive_obj.poolID == poolID

        expected_size = drive_obj.getNextVolumeSize(
            drive_info['physical'], drive_info['capacity'])
        assert expected_size == newSize

        assert expected_size == volInfo['newSize']
        assert drive_obj.name == volInfo['name']

        if drive_obj.isDiskReplicationInProgress():
            assert drive_obj.diskReplicate['domainID'] == \
                volInfo['domainID']
            assert drive_obj.diskReplicate['imageID'] == \
                volInfo['imageID']
            assert drive_obj.diskReplicate['poolID'] == \
                volInfo['poolID']
            assert drive_obj.diskReplicate['volumeID'] == \
                volInfo['volumeID']
        else:
            assert drive_obj.domainID == volInfo['domainID']
            assert drive_obj.imageID == volInfo['imageID']
            assert drive_obj.poolID == volInfo['poolID']
            assert drive_obj.volumeID == volInfo['volumeID']


class TestDiskExtensionWithPolling(DiskExtensionTestBase):

    def test_no_extension_allocation_below_watermark(self):
        with make_env(
                events_enabled=False,
                drive_infos=self.DRIVE_INFOS) as (testvm, dom, drives):
            vda = dom.block_info['/virtio/0']
            vda['allocation'] = 0 * MiB
            vdb = dom.block_info['/virtio/1']
            vdb['allocation'] = allocation_threshold_for_resize_mb(
                vdb, drives[1]) - 1 * MiB

            extended = testvm.monitor_drives()

        assert extended is False

    def test_no_extension_maximum_size_reached(self):
        with make_env(
                events_enabled=False,
                drive_infos=self.DRIVE_INFOS) as (testvm, dom, drives):
            vda = dom.block_info['/virtio/0']
            vda['allocation'] = 0 * MiB
            vdb = dom.block_info['/virtio/1']
            max_size = drives[1].getMaxVolumeSize(vdb['capacity'])
            vdb['allocation'] = max_size
            vdb['physical'] = max_size
            extended = testvm.monitor_drives()

        assert extended is False

    def test_extend_drive_allocation_crosses_watermark_limit(self):
        with make_env(
                events_enabled=False,
                drive_infos=self.DRIVE_INFOS) as (testvm, dom, drives):
            vda = dom.block_info['/virtio/0']
            vda['allocation'] = 0 * MiB
            vdb = dom.block_info['/virtio/1']
            vdb['allocation'] = allocation_threshold_for_resize_mb(
                vdb, drives[1]) + 1 * MiB

            extended = testvm.monitor_drives()

        assert extended is True
        assert len(testvm.cif.irs.extensions) == 1
        self.check_extension(vdb, drives[1], testvm.cif.irs.extensions[0])

    def test_extend_drive_allocation_equals_next_size(self):
        with make_env(
                events_enabled=False,
                drive_infos=self.DRIVE_INFOS) as (testvm, dom, drives):
            vda = dom.block_info['/virtio/0']
            vda['allocation'] = drives[0].getNextVolumeSize(
                vda['physical'], vda['capacity'])
            vdb = dom.block_info['/virtio/1']
            vdb['allocation'] = 0 * MiB
            extended = testvm.monitor_drives()

        assert extended is True
        assert len(testvm.cif.irs.extensions) == 1
        self.check_extension(vda, drives[0], testvm.cif.irs.extensions[0])

    def test_stop_extension_loop_on_improbable_request(self):
        with make_env(
                events_enabled=False,
                drive_infos=self.DRIVE_INFOS) as (testvm, dom, drives):
            vda = dom.block_info['/virtio/0']
            vda['allocation'] = (
                drives[0].getNextVolumeSize(
                    vda['physical'], vda['capacity']) + 1 * MiB)
            vdb = dom.block_info['/virtio/1']
            vdb['allocation'] = 0 * MiB
            extended = testvm.monitor_drives()

        assert extended is False
        assert dom.info()[0] == libvirt.VIR_DOMAIN_PAUSED

    # TODO: add the same test for disk replicas.
    def test_vm_resumed_after_drive_extended(self):
        with make_env(
                events_enabled=False,
                drive_infos=self.DRIVE_INFOS) as (testvm, dom, drives):
            testvm.pause()

            vda = dom.block_info['/virtio/0']
            vda['allocation'] = 0 * MiB
            vdb = dom.block_info['/virtio/1']  # shortcut
            vdb['allocation'] = allocation_threshold_for_resize_mb(
                vdb, drives[1]) + 1 * MiB

            extended = testvm.monitor_drives()
            assert extended is True
            assert len(testvm.cif.irs.extensions) == 1

            # Simulate completed extend operation, invoking callback

            simulate_extend_callback(testvm.cif.irs, extension_id=0)

        assert testvm.lastStatus == vmstatus.UP
        assert dom.info()[0] == libvirt.VIR_DOMAIN_RUNNING

    # TODO: add test with storage failures in the extension flow


@expandPermutations
class TestDiskExtensionWithEvents(DiskExtensionTestBase):

    # TODO: missing tests:
    # - call extend_if_needed when drive.threshold_state is EXCEEDED
    #   -> extend
    # FIXME: already covered by existing cases?

    def test_extend_using_events(self):
        with make_env(
                events_enabled=True,
                drive_infos=self.DRIVE_INFOS) as (testvm, dom, drives):

            # first run: does nothing but set the block thresholds
            testvm.monitor_drives()

            # Simulate writing to drive vdb
            vdb = dom.block_info['/virtio/1']

            alloc = allocation_threshold_for_resize_mb(
                vdb, drives[1]) + 1 * MiB

            vdb['allocation'] = alloc

            # Simulating block threshold event
            testvm.drive_monitor.on_block_threshold(
                'vdb', '/virtio/1', alloc, 1 * MiB)

            drv = drives[1]
            assert drv.threshold_state == BLOCK_THRESHOLD.EXCEEDED

            # Simulating periodic check
            extended = testvm.monitor_drives()
            assert extended is True
            assert len(testvm.cif.irs.extensions) == 1
            self.check_extension(vdb, drives[1], testvm.cif.irs.extensions[0])
            assert drv.threshold_state == BLOCK_THRESHOLD.EXCEEDED

            # Simulate completed extend operation, invoking callback

            simulate_extend_callback(testvm.cif.irs, extension_id=0)

            drv = drives[1]
            assert drv.threshold_state == BLOCK_THRESHOLD.SET

    @permutations([
        # drive_conf, expected_state, threshold
        # the threshold values depend on the physical size defined in the test,
        # and on the mock config.
        #
        # non-chunked drive
        ((drive_config(
            format='cow',
            diskType=DISK_TYPE.FILE),
         {'capacity': 4 * GiB, 'allocation': 2 * GiB, 'physical': 2 * GiB}),
         BLOCK_THRESHOLD.UNSET, None),
        # ditto
        ((drive_config(
            format='raw',
            diskType=DISK_TYPE.BLOCK),
         {'capacity': 2 * GiB, 'allocation': 0 * GiB, 'physical': 2 * GiB}),
         BLOCK_THRESHOLD.UNSET, None),
        # ditto
        ((drive_config(
            format='raw',
            diskType=DISK_TYPE.FILE),
         {'capacity': 2 * GiB, 'allocation': 0 * GiB, 'physical': 2 * GiB}),
         BLOCK_THRESHOLD.UNSET, None),
        # ditto
        ((drive_config(
            format='raw',
            diskType=DISK_TYPE.NETWORK),
         {'capacity': 2 * GiB, 'allocation': 0 * GiB, 'physical': 2 * GiB}),
         BLOCK_THRESHOLD.UNSET, None),
        # non-chunked drive replicating to non-chunked drive
        ((drive_config(
            format='cow',
            diskType=DISK_TYPE.FILE,
            diskReplicate={
                'format': 'cow',
                'diskType': DISK_TYPE.FILE}),
         {'capacity': 4 * GiB, 'allocation': 2 * GiB, 'physical': 2 * GiB}),
         BLOCK_THRESHOLD.UNSET, None),
        # non-chunked drive replicating to chunked-drive
        #
        # TODO:
        # Here the replica size should be bigger than the source drive size.
        # Possible setup:
        # source: allocation=1, physical=1
        # replica: allocation=1, physical=3
        # Currently we assume that drive size is same as replica size.
        ((drive_config(
            format='cow',
            diskType=DISK_TYPE.FILE,
            diskReplicate={
                'format': 'cow',
                'diskType': DISK_TYPE.BLOCK}),
         {'capacity': 4 * GiB, 'allocation': 2 * GiB, 'physical': 2 * GiB}),
         BLOCK_THRESHOLD.SET, 1 * GiB),
        # chunked drive
        ((drive_config(
            format='cow',
            diskType=DISK_TYPE.BLOCK),
         {'capacity': 4 * GiB, 'allocation': 1 * GiB, 'physical': 2 * GiB}),
         BLOCK_THRESHOLD.SET, 1536 * MiB),
        # chunked drive replicating to chunked drive
        ((drive_config(
            format='cow',
            diskType=DISK_TYPE.BLOCK,
            diskReplicate={
                'format': 'cow',
                'diskType': DISK_TYPE.BLOCK}),
         {'capacity': 4 * GiB, 'allocation': 1 * GiB, 'physical': 3 * GiB}),
         BLOCK_THRESHOLD.SET, 2 * GiB),
        # chunked drive replicating to non-chunked drive
        ((drive_config(
            format='cow',
            diskType=DISK_TYPE.BLOCK,
            diskReplicate={
                'format': 'cow',
                'diskType': DISK_TYPE.FILE}),
         {'capacity': 4 * GiB, 'allocation': 1 * GiB, 'physical': 3 * GiB}),
         BLOCK_THRESHOLD.SET, 2 * GiB),
    ])
    def test_set_new_threshold_when_state_unset(self, drive_info,
                                                expected_state, threshold):
        with make_env(events_enabled=True,
                      drive_infos=[drive_info]) as (testvm, dom, drives):

            vda = drives[0]  # shortcut

            assert vda.threshold_state == BLOCK_THRESHOLD.UNSET
            # first run: does nothing but set the block thresholds

            testvm.drive_monitor.update_threshold_state_exceeded = \
                lambda *args: None

            testvm.monitor_drives()

            assert vda.threshold_state == expected_state
            if threshold is not None:
                assert dom.thresholds[vda.name] == threshold

    def test_set_new_threshold_when_state_unset_but_fails(self):
        with make_env(
                events_enabled=True,
                drive_infos=self.DRIVE_INFOS) as (testvm, dom, drives):

            for drive in drives:
                assert drive.threshold_state == BLOCK_THRESHOLD.UNSET

            # Simulate setBlockThreshold failure
            testvm._dom.errors["setBlockThreshold"] = fake.Error(
                libvirt.VIR_ERR_OPERATION_FAILED, "fake error")

            # first run: does nothing but set the block thresholds
            testvm.monitor_drives()

            for drive in drives:
                assert drive.threshold_state == BLOCK_THRESHOLD.UNSET

    def test_set_new_threshold_when_state_set(self):
        # Vm.monitor_drives must not pick up drives with
        # threshold_state == SET, so we call
        # Vm.extend_drive_if_needed explictely
        with make_env(
                events_enabled=True,
                drive_infos=self.DRIVE_INFOS) as (testvm, dom, drives):

            drives[0].threshold_state = BLOCK_THRESHOLD.SET

            extended = testvm.extend_drive_if_needed(drives[0])

            assert not extended

    @permutations([
        # events_enabled, expected_state
        (True, BLOCK_THRESHOLD.EXCEEDED),
        (False, BLOCK_THRESHOLD.UNSET),
    ])
    def test_force_drive_threshold_state_exceeded(self, events_enabled,
                                                  expected_state):
        with make_env(
                events_enabled=events_enabled,
                drive_infos=self.DRIVE_INFOS) as (testvm, dom, drives):
            # Simulate event not received. Possible cases:
            # - the handling of the event in Vdsm was delayed because some
            #   blocking code was called from the libvirt event loop
            #   (unavoidable race)
            # - block threshold set by below the current allocation
            #   (also unavoidable race)

            vda = dom.block_info['/virtio/0']
            vda['allocation'] = allocation_threshold_for_resize_mb(
                vda, drives[0]) + 1 * MiB

            testvm.monitor_drives()

            # forced to exceeded by monitor_drives() even if no
            # event received.
            assert drives[0].threshold_state == expected_state

    def test_event_received_before_write_completes(self):
        # QEMU submits an event when write is attempted, so it
        # is possible that at the time we receive the event the
        # the write was not completed yet, or failed, and the
        # volume size is still bellow the threshold.
        # We will not extend the drive, but keep it marked for
        # extension.
        with make_env(
                events_enabled=True,
                drive_infos=self.DRIVE_INFOS) as (testvm, dom, drives):
            # NOTE: write not yet completed, so the allocation value
            # for the drive must me below than the value reported in
            # the event.
            vda = dom.block_info['/virtio/0']

            alloc = allocation_threshold_for_resize_mb(
                vda, drives[0]) + 1 * MiB

            # Simulating block threshold event
            testvm.drive_monitor.on_block_threshold(
                'vda', '/virtio/0', alloc, 1 * MiB)

            testvm.monitor_drives()

            # The threshold state is correctly kept as exceeded, so extension
            # will be tried again next cycle.
            assert drives[0].threshold_state == \
                BLOCK_THRESHOLD.EXCEEDED

    def test_block_threshold_set_failure_after_drive_extended(self):
        with make_env(
                events_enabled=True,
                drive_infos=self.DRIVE_INFOS) as (testvm, dom, drives):

            # first run: does nothing but set the block thresholds
            testvm.monitor_drives()

            # Simulate write on drive vdb
            vdb = dom.block_info['/virtio/1']

            # The BLOCK_THRESHOLD event contains the highest allocated
            # block...
            alloc = allocation_threshold_for_resize_mb(
                vdb, drives[1]) + 1 * MiB

            # ... but we repeat the check in monitor_drives(),
            # so we need to set both locations to the correct value.
            vdb['allocation'] = alloc

            # Simulating block threshold event
            testvm.drive_monitor.on_block_threshold(
                'vdb', '/virtio/1', alloc, 1 * MiB)

            # Simulating periodic check
            testvm.monitor_drives()
            assert len(testvm.cif.irs.extensions) == 1

            # Simulate completed extend operation, invoking callback

            # Simulate setBlockThreshold failure
            testvm._dom.errors["setBlockThreshold"] = fake.Error(
                libvirt.VIR_ERR_OPERATION_FAILED, "fake error")

            simulate_extend_callback(testvm.cif.irs, extension_id=0)

            drv = drives[1]
            assert drv.threshold_state == BLOCK_THRESHOLD.UNSET


class TestReplication(DiskExtensionTestBase):
    """
    Test extension during replication.

    We have several cases:
    - replicating from chunked to chunked: extend the replica, then the volume.
    - replicating from non-chunked to chunked: extend the replica.
    - replicating from chunked to non-chunked: extend the volume.

    When extending the replica completed, we have these cases:
    - replication finished during the extend:
      - abort the extend
      - drive was UNSET by the pivot.
    - replication failed during the extend:
      - extend the volume
      - drive remains EXCCEEDED.

    When extending the replica failed, we have these cases:
    - replication finished during the extend
      - abort the extend
      - drive was unset by the pivot
    - replication failed during the extend
      - abort the extend
      - drive remains EXCEEDED.

    When extending the volume completed, we have these cases:
    - replication finished during the extend:
      - abort the extend
      - drive was UNSET by the pivot
    - replication failed during the extend:
      - update drive size and set a new threshold.

    When extending the volume failed:
    - regardless of replication state, drive must remain EXCCEEDED
    """


class FakeVM(vm.Vm):

    log = logging.getLogger('test')

    def __init__(self, cif, dom, disks):
        self.id = 'drive_monitor_vm'
        self.cif = cif
        self.drive_monitor = drivemonitor.DriveMonitor(self, self.log)
        self._dom = dom
        self._devices = {hwclass.DISK: disks}

        # needed for pause()/cont()

        self._lastStatus = vmstatus.UP
        self._guestCpuRunning = True
        self._custom = {}
        self._confLock = threading.Lock()
        self.conf = {}
        self._guestCpuLock = TimedAcquireLock(self.id)
        self._resume_behavior = 'auto_resume'
        self._pause_time = None

    # to reduce the amount of faking needed, we fake those methods
    # which are not relevant to the monitor_drives() flow

    def send_status_event(self, **kwargs):
        pass

    def isMigrating(self):
        return False

    def _update_metadata(self):
        pass


class FakeDomain(object):

    def __init__(self):
        self._state = (libvirt.VIR_DOMAIN_RUNNING, )
        self.block_info = {}
        self.errors = {}
        self.thresholds = {}

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

    @maybefail
    def setBlockThreshold(self, dev, threshold):
        self.thresholds[dev] = threshold


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

    # testing helper
    def set_drive_size(self, drive, capacity):
        key = (drive.domainID, drive.poolID,
               drive.imageID, drive.volumeID)
        self.volume_sizes[key] = capacity

        if drive.isDiskReplicationInProgress():
            replica = drive.diskReplicate
            key = (replica['domainID'], replica['poolID'],
                   replica['imageID'], replica['volumeID'])
            self.volume_sizes[key] = capacity


def make_drive(log, dom, irs, index, conf, block_info):
    cfg = utils.picklecopy(conf)

    cfg['index'] = index
    cfg['path'] = '/{iface}/{index}'.format(
        iface=cfg['iface'], index=cfg['index']
    )

    add_uuids(index, cfg)
    if 'diskReplicate' in cfg:
        add_uuids(index + REPLICA_BASE_INDEX, cfg['diskReplicate'])

    drive = Drive(log, **cfg)

    dom.block_info[drive.path] = utils.picklecopy(block_info)

    if (drive.format == "raw" and
            block_info["physical"] != block_info["capacity"]):
        raise RuntimeError(
            "Invalid test data - "
            "raw disk capacity != physical: %s" % block_info)

    irs.set_drive_size(drive, block_info['physical'])

    return drive


def add_uuids(index, conf):
    # storage does not validate the UUIDs, so we use phony names
    # for brevity
    conf['volumeID'] = 'volume_%d' % index
    conf['imageID'] = 'image_%d' % index
    # intentionally constant
    conf['poolID'] = 'pool_0'
    conf['domainID'] = 'domain_0'


def simulate_extend_callback(irs, extension_id):
    poolID, volInfo, newSize, func = irs.extensions[extension_id]
    key = (volInfo['domainID'], volInfo['poolID'],
           volInfo['imageID'], volInfo['volumeID'])
    # Simulate refresh, updating local volume size
    irs.volume_sizes[key] = newSize

    func(volInfo)

    # Calling refreshVolume is critical in this flow.
    # Check this indeed happened.
    if key != irs.refreshes[extension_id]:
        raise AssertionError('Volume %s not refreshed' % key)
