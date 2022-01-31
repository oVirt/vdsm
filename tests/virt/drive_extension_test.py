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

"""
Testng extension during replication may not be implemented yet.

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

import logging
import threading

import xml.etree.ElementTree as etree

import libvirt
import pytest

from vdsm import utils
from vdsm.common import response
from vdsm.common.units import MiB, GiB
from vdsm.virt.vmdevices.storage import Drive, DISK_TYPE, BLOCK_THRESHOLD
from vdsm.virt.vmdevices import hwclass
from vdsm.virt.utils import TimedAcquireLock
from vdsm.virt import thinp
from vdsm.virt import vmstatus
from vdsm.virt.vm import Vm

from testlib import maybefail

from . import vmfakelib as fake

CHUNK_SIZE = 1 * GiB
CHUNK_PCT = 50
REPLICA_BASE_INDEX = 1000


# TODO: factor out this function and its counterpart in vmstorage_test.py
def drive_config(**kw):
    ''' Return drive configuration updated from **kw '''
    conf = {
        'name': 'vda',
        'device': 'disk',
        'format': 'raw',
        'iface': 'virtio',
        'index': 0,
        'propagateErrors': 'off',
        'readonly': 'False',
        'shared': 'none',
        'type': 'disk',
    }
    conf.update(kw)
    return conf


def block_info(
        name="vda", path="/virtio/0", backingIndex=1, capacity=4 * GiB,
        allocation=0, physical=4 * GiB, threshold=0):
    return {
        "name": name,
        "path": path,
        "backingIndex": backingIndex,
        "capacity": capacity,
        "allocation": allocation,
        "physical": physical,
        "threshold": threshold,
    }


def drive_infos():
    return (
        (
            drive_config(
                name="vda",
                index=0,
                format='cow',
                diskType=DISK_TYPE.BLOCK,
            ),
            block_info(
                name="vda",
                path="/virtio/0",
                # libvirt starts backingIndex at 1.
                backingIndex=1,
                allocation=1 * GiB,
                physical=2 * GiB,
            ),
        ),
        (
            drive_config(
                name="vdb",
                index=1,
                format='cow',
                diskType=DISK_TYPE.BLOCK,
            ),
            block_info(
                name="vdb",
                path="/virtio/1",
                backingIndex=2,
                allocation=1 * GiB,
                physical=2 * GiB,
            ),
        ),
    )


@pytest.fixture
def tmp_config(monkeypatch):
    # the Drive class use those two tunables as class constants.
    monkeypatch.setattr(Drive, 'VOLWM_CHUNK_SIZE', CHUNK_SIZE)
    monkeypatch.setattr(Drive, 'VOLWM_FREE_PCT', CHUNK_PCT)


def allocation_threshold_for_resize_mb(block_info, drive):
    return block_info['physical'] - drive.watermarkLimit


def check_extension(drive_info, drive_obj, extension_req):
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
        replica = drive_obj.diskReplicate
        assert replica['domainID'] == volInfo['domainID']
        assert replica['imageID'] == volInfo['imageID']
        assert replica['poolID'] == volInfo['poolID']
        assert replica['volumeID'] == volInfo['volumeID']
    else:
        assert drive_obj.domainID == volInfo['domainID']
        assert drive_obj.imageID == volInfo['imageID']
        assert drive_obj.poolID == volInfo['poolID']
        assert drive_obj.volumeID == volInfo['volumeID']

# TODO: missing tests:
# - call extend_if_needed when drive.threshold_state is EXCEEDED
#   -> extend
# FIXME: already covered by existing cases?


def test_extend(tmp_config):
    vm = FakeVM(drive_infos())
    drives = vm.getDiskDevices()
    drv = drives[1]

    # first run: does nothing but set the block thresholds
    vm.monitor_volumes()

    # Simulate writing to drive vdb
    vdb = vm.block_stats[2]

    alloc = allocation_threshold_for_resize_mb(vdb, drv) + 1 * MiB

    vdb['allocation'] = alloc

    assert drv.threshold_state == BLOCK_THRESHOLD.SET

    # Check that the double event for top volume is ignored.
    vm.volume_monitor.on_block_threshold(
        'vdb', '/virtio/1', alloc, 1 * MiB)
    assert drv.threshold_state == BLOCK_THRESHOLD.SET

    # Simulating block threshold event
    vm.volume_monitor.on_block_threshold(
        'vdb[1]', '/virtio/1', alloc, 1 * MiB)
    assert drv.threshold_state == BLOCK_THRESHOLD.EXCEEDED

    # Simulating periodic check
    extended = vm.monitor_volumes()
    assert extended is True
    assert len(vm.cif.irs.extensions) == 1
    check_extension(vdb, drives[1], vm.cif.irs.extensions[0])
    assert drv.threshold_state == BLOCK_THRESHOLD.EXCEEDED

    # Simulate completed extend operation, invoking callback

    simulate_extend_callback(vm.cif.irs, extension_id=0)

    assert drv.threshold_state == BLOCK_THRESHOLD.SET


def test_extend_no_allocation(tmp_config):
    vm = FakeVM(drive_infos())
    drives = vm.getDiskDevices()

    # first run: does nothing but set the block thresholds
    vm.monitor_volumes()

    # Simulate writing to drive vdb
    vdb = vm.block_stats[2]

    # Simulate libvirt bug when alloction is not reported during backup.
    # https://bugzilla.redhat.com/2015281
    vdb['allocation'] = 0

    drv = drives[1]

    # Simulating block threshold event
    vm.volume_monitor.on_block_threshold(
        'vdb[1]', '/virtio/1', 0, 1 * MiB)
    assert drv.threshold_state == BLOCK_THRESHOLD.EXCEEDED

    # Simulating periodic check
    extended = vm.monitor_volumes()
    assert extended is True
    assert len(vm.cif.irs.extensions) == 1
    check_extension(vdb, drives[1], vm.cif.irs.extensions[0])
    assert drv.threshold_state == BLOCK_THRESHOLD.EXCEEDED


@pytest.mark.parametrize("drive_info,expected_state,threshold", [
    # the threshold values depend on the physical size defined in the test,
    # and on the mock config.

    pytest.param(
        (
            drive_config(format='cow', diskType=DISK_TYPE.FILE),
            block_info(),
        ),
        BLOCK_THRESHOLD.UNSET,
        None,
        id="cow-file",
    ),

    pytest.param(
        (
            drive_config(format='raw', diskType=DISK_TYPE.BLOCK),
            block_info(physical=4 * GiB),
        ),
        BLOCK_THRESHOLD.UNSET,
        None,
        id="raw-block",
    ),

    pytest.param(
        (
            drive_config(format='raw', diskType=DISK_TYPE.FILE),
            block_info(physical=4 * GiB),
        ),
        BLOCK_THRESHOLD.UNSET,
        None,
        id="raw-file",
    ),

    pytest.param(
        (
            drive_config(format='raw', diskType=DISK_TYPE.NETWORK),
            block_info(physical=4 * GiB),
        ),
        BLOCK_THRESHOLD.UNSET,
        None,
        id="raw-network",
    ),

    pytest.param(
        (
            drive_config(
                format='cow',
                diskType=DISK_TYPE.FILE,
                diskReplicate={
                    'format': 'cow',
                    'diskType': DISK_TYPE.FILE,
                },
            ),
            block_info(allocation=2 * GiB, physical=2 * GiB),
        ),
        BLOCK_THRESHOLD.UNSET,
        None,
        id="replicate-to-file",
    ),

    # TODO:
    # Here the replica size should be bigger than the source drive size.
    # Possible setup:
    # source: allocation=1, physical=1
    # replica: allocation=1, physical=3
    # Currently we assume that drive size is same as replica size.
    pytest.param(
        (
            drive_config(
                format='cow',
                diskType=DISK_TYPE.FILE,
                diskReplicate={
                    'format': 'cow',
                    'diskType': DISK_TYPE.BLOCK,
                },
            ),
            block_info(allocation=2 * GiB, physical=2 * GiB),
        ),
        BLOCK_THRESHOLD.SET,
        1 * GiB,
        id="replicate-to-block",
    ),

    pytest.param(
        (
            drive_config(format='cow', diskType=DISK_TYPE.BLOCK),
            block_info(allocation=1 * GiB, physical=2 * GiB),
        ),
        BLOCK_THRESHOLD.SET,
        1536 * MiB,
        id="cow-block",
    ),

    pytest.param(
        (
            drive_config(
                format='cow',
                diskType=DISK_TYPE.BLOCK,
                diskReplicate={
                    'format': 'cow',
                    'diskType': DISK_TYPE.BLOCK,
                },
            ),
            block_info(allocation=1 * GiB, physical=3 * GiB),
        ),
        BLOCK_THRESHOLD.SET,
        2 * GiB,
        id="cow-block-replicate-to-cow-block",
    ),

    pytest.param(
        (
            drive_config(
                format='cow',
                diskType=DISK_TYPE.BLOCK,
                diskReplicate={
                    'format': 'cow',
                    'diskType': DISK_TYPE.FILE,
                },
            ),
            block_info(allocation=1 * GiB, physical=3 * GiB),
        ),
        BLOCK_THRESHOLD.SET,
        2 * GiB,
        id="cow-block-replicate-to-cow-file",
    ),
])
def test_set_new_threshold_when_state_unset(
        tmp_config, drive_info, expected_state, threshold):
    vm = FakeVM([drive_info])
    drives = vm.getDiskDevices()

    vda = drives[0]  # shortcut

    assert vda.threshold_state == BLOCK_THRESHOLD.UNSET
    # first run: does nothing but set the block thresholds

    vm.volume_monitor.update_threshold_state_exceeded = \
        lambda *args: None

    vm.monitor_volumes()

    assert vda.threshold_state == expected_state
    if threshold is not None:
        assert vm._dom.thresholds["vda[1]"] == threshold


def test_set_new_threshold_when_state_unset_but_fails(tmp_config):
    vm = FakeVM(drive_infos())
    drives = vm.getDiskDevices()

    for drive in drives:
        assert drive.threshold_state == BLOCK_THRESHOLD.UNSET

    # Simulate setBlockThreshold failure
    vm._dom.errors["setBlockThreshold"] = fake.Error(
        libvirt.VIR_ERR_OPERATION_FAILED, "fake error")

    # first run: does nothing but set the block thresholds
    vm.monitor_volumes()

    for drive in drives:
        assert drive.threshold_state == BLOCK_THRESHOLD.UNSET


def test_set_new_threshold_when_state_set(tmp_config):
    # Vm.monitor_volumes must not pick up drives with
    # threshold_state == SET, so we call
    # VolumeMonitor._extend_drive_if_needed explictely.
    # TODO: Test without this calling it.
    vm = FakeVM(drive_infos())
    mon = vm.volume_monitor
    drives = vm.getDiskDevices()

    drives[0].threshold_state = BLOCK_THRESHOLD.SET

    block_stats = mon.get_block_stats()
    extended = mon._extend_drive_if_needed(drives[0], block_stats)

    assert not extended


def test_force_drive_threshold_state_exceeded(tmp_config):
    vm = FakeVM(drive_infos())

    # Simulate event not received. Possible cases:
    # - the handling of the event in Vdsm was delayed because some
    #   blocking code was called from the libvirt event loop
    #   (unavoidable race)
    # - block threshold set by below the current allocation
    #   (also unavoidable race)

    drives = vm.getDiskDevices()

    vda = vm.block_stats[1]
    vda['allocation'] = allocation_threshold_for_resize_mb(
        vda, drives[0]) + 1 * MiB

    vm.monitor_volumes()

    # forced to exceeded by monitor_volumes() even if no
    # event received.
    assert drives[0].threshold_state == BLOCK_THRESHOLD.EXCEEDED


def test_event_received_before_write_completes(tmp_config):
    # QEMU submits an event when write is attempted, so it
    # is possible that at the time we receive the event the
    # the write was not completed yet, or failed, and the
    # volume size is still bellow the threshold.
    # We will not extend the drive, but keep it marked for
    # extension.
    vm = FakeVM(drive_infos())
    drives = vm.getDiskDevices()

    # NOTE: write not yet completed, so the allocation value
    # for the drive must me below than the value reported in
    # the event.
    vda = vm.block_stats[1]

    alloc = allocation_threshold_for_resize_mb(
        vda, drives[0]) + 1 * MiB

    drv = drives[0]
    assert drv.threshold_state == BLOCK_THRESHOLD.UNSET

    # Check that the double event for top volume is ignored.
    vm.volume_monitor.on_block_threshold(
        'vda', '/virtio/0', alloc, 1 * MiB)
    assert drv.threshold_state == BLOCK_THRESHOLD.UNSET

    # Simulating block threshold event
    vm.volume_monitor.on_block_threshold(
        'vda[0]', '/virtio/0', alloc, 1 * MiB)
    assert drv.threshold_state == BLOCK_THRESHOLD.EXCEEDED

    vm.monitor_volumes()

    # The threshold state is correctly kept as exceeded, so extension
    # will be tried again next cycle.
    assert drv.threshold_state == BLOCK_THRESHOLD.EXCEEDED


def test_block_threshold_set_failure_after_drive_extended(tmp_config):
    vm = FakeVM(drive_infos())
    drives = vm.getDiskDevices()

    # first run: does nothing but set the block thresholds
    vm.monitor_volumes()

    # Simulate write on drive vdb
    vdb = vm.block_stats[2]

    # The BLOCK_THRESHOLD event contains the highest allocated
    # block...
    alloc = allocation_threshold_for_resize_mb(
        vdb, drives[1]) + 1 * MiB

    # ... but we repeat the check in monitor_volumes(),
    # so we need to set both locations to the correct value.
    vdb['allocation'] = alloc

    drv = drives[1]
    assert drv.threshold_state == BLOCK_THRESHOLD.SET

    # Check that the double event for top volume is ignored.
    vm.volume_monitor.on_block_threshold(
        'vdb', '/virtio/1', alloc, 1 * MiB)
    assert drv.threshold_state == BLOCK_THRESHOLD.SET

    # Simulating block threshold event
    vm.volume_monitor.on_block_threshold(
        'vdb[1]', '/virtio/1', alloc, 1 * MiB)
    assert drv.threshold_state == BLOCK_THRESHOLD.EXCEEDED

    # Simulating periodic check
    vm.monitor_volumes()
    assert len(vm.cif.irs.extensions) == 1

    # Simulate completed extend operation, invoking callback

    # Simulate setBlockThreshold failure
    vm._dom.errors["setBlockThreshold"] = fake.Error(
        libvirt.VIR_ERR_OPERATION_FAILED, "fake error")

    simulate_extend_callback(vm.cif.irs, extension_id=0)

    assert drv.threshold_state == BLOCK_THRESHOLD.UNSET


class FakeVM(Vm):

    log = logging.getLogger('test')

    def __init__(self, drive_infos):
        self._dom = FakeDomain()
        self.cif = FakeClientIF(FakeIRS())
        self.id = 'volume_monitor_vm'
        self.volume_monitor = thinp.VolumeMonitor(self, self.log)
        self.block_stats = {}

        disks = []
        for drive_conf, block_info in drive_infos:
            drive = make_drive(self.log, drive_conf, block_info)
            self.cif.irs.set_drive_size(drive, block_info['physical'])
            self._dom.add_drive(drive, block_info)
            disks.append(drive)
            self.block_stats[block_info["backingIndex"]] = block_info

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
    # which are not relevant to the monitor_volumes() flow

    def send_status_event(self, **kwargs):
        pass

    def _update_metadata(self):
        pass

    def should_refresh_destination_volume(self):
        return False

    def get_block_stats(self):
        # Create libvirt response.
        raw_stats = {"block.count": len(self.block_stats)}
        for i, block_info in enumerate(self.block_stats.values()):
            raw_stats[f"block.{i}.name"] = block_info["name"]
            raw_stats[f"block.{i}.backingIndex"] = block_info["backingIndex"]
            raw_stats[f"block.{i}.path"] = block_info["path"]
            raw_stats[f"block.{i}.capacity"] = block_info["capacity"]
            raw_stats[f"block.{i}.physical"] = block_info["physical"]
            raw_stats[f"block.{i}.allocation"] = block_info["allocation"]
            raw_stats[f"block.{i}.threshold"] = block_info["threshold"]
        return raw_stats


class FakeDomain(object):

    def __init__(self):
        self._devices = etree.Element('devices')
        self._state = (libvirt.VIR_DOMAIN_RUNNING, )
        self.errors = {}
        self.thresholds = {}

    # The following is needed in the 'pause' flow triggered
    # by the ImprobableResizeRequestError

    def XMLDesc(self, flags=0):
        domain = etree.Element("domain")
        domain.append(self._devices)
        return etree.tostring(domain).decode()

    def suspend(self):
        self._state = (libvirt.VIR_DOMAIN_PAUSED, )

    def resume(self):
        self._state = (libvirt.VIR_DOMAIN_RUNNING, )

    def info(self):
        return self._state

    @maybefail
    def setBlockThreshold(self, target, threshold):
        self.thresholds[target] = threshold

    # Testing API.

    def add_drive(self, drive, block_info):
        """
        Add minimal xml to make the drive work with
        Vm.query_drive_volume_chain().

            <disk type='block'>
                <source dev='/virtio/1' index='2'/>
                <backingStore/>
                <alias name='alias_1'/>
            </disk>
        """
        index = block_info["backingIndex"]
        disk = self._devices.find(
            "./disk/source[@index='{}']".format(index))
        if disk is not None:
            disk_xml = etree.tostring(disk).decode()
            raise RuntimeError(
                "Disk already exists: {}".format(disk_xml))

        disk = etree.SubElement(self._devices, "disk", type=drive.diskType)

        # Not correct for network drives, but good enough since we don't
        # monitor them.
        path_attr = "dev" if drive.diskType == "block" else "file"

        extra = {path_attr: drive.path, "index": str(index)}
        etree.SubElement(disk, "source", **extra)
        etree.SubElement(disk, "backingStore")
        etree.SubElement(disk, "alias", name=drive.alias)


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
        return response.success()

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


def make_drive(log, drive_conf, block_info):
    cfg = utils.picklecopy(drive_conf)

    cfg['path'] = block_info['path']
    cfg['alias'] = 'alias_%d' % cfg["index"]

    add_uuids(cfg["index"], cfg)

    if 'diskReplicate' in cfg:
        add_uuids(cfg["index"] + REPLICA_BASE_INDEX, cfg['diskReplicate'])

    cfg["volumeChain"] = [{"path": cfg["path"], "volumeID": cfg["volumeID"]}]

    drive = Drive(log, **cfg)

    if (drive.format == "raw" and
            block_info["physical"] != block_info["capacity"]):
        raise RuntimeError(
            "Invalid test data - "
            "raw disk capacity != physical: %s" % block_info)

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
