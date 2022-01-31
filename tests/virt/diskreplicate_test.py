#
# Copyright 2019 Red Hat, Inc.
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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#

from __future__ import absolute_import
from __future__ import division

import logging
import pytest

import vdsm.virt.vmdevices.storage as storage

from vdsm.clientIF import clientIF
from vdsm.common import response
from vdsm.common import exception
from vdsm.virt import vm
from vdsm.virt.vmdevices import hwclass


log = logging.getLogger()


src_drive_conf = {
    'device': 'disk',
    'iface': 'virtio',
    'path': '/path/to/volume',
    'type': 'disk',
    'index': 0,
    'domainID': 'src-domain-id',
    'imageID': 'imgID',
    'volumeID': 'volumeID',
    'diskReplicate': '',
    'name': 'vda',
}

dst_drive_conf = {
    'device': 'disk',
    'iface': 'virtio',
    'path': '/path/to/volume',
    'type': 'disk',
    'index': 0,
    'domainID': 'dst-domain-id',
    'imageID': 'imgID',
    'volumeID': 'volumeID',
    'name': 'vda',
}

lease_drive_conf = {
    'device': 'disk',
    'iface': 'virtio',
    'path': '/path/to/volume',
    'type': 'disk',
    'index': 0,
    'domainID': 'src-domain-id',
    'imageID': 'imgID',
    'volumeID': 'volumeID',
    'diskReplicate': '',
    'name': 'vda',
    'volumeChain': [{
        'leasePath': 'path',
        'leaseOffset': 'offset',
    }],
}


def test_lookup_error():
    _vm = FakeVm()
    result = _vm.diskReplicateFinish(src_drive_conf, dst_drive_conf)

    assert result == response.error('imageErr')


def test_has_volume_leases():
    _vm = FakeVm([make_drive(lease_drive_conf)])
    result = _vm.diskReplicateFinish(lease_drive_conf, dst_drive_conf)
    assert result == response.error('noimpl')


def test_diskreplicatefinish_transient_disk():
    src_drive = make_drive(src_drive_conf,
                           storage.DRIVE_SHARED_TYPE.TRANSIENT)
    _vm = FakeVm([src_drive])
    result = _vm.diskReplicateFinish(src_drive_conf, dst_drive_conf)

    assert result == response.error("transientErr")


def test_diskreplicatefinish_replication_not_in_progress():
    # Passing dst_drive conf as src as it does not have the diskReplicate
    # attribute
    _vm = FakeVm([make_drive(dst_drive_conf)])
    src = dst_drive_conf

    with pytest.raises(exception.ReplicationNotInProgress):
        _vm.diskReplicateFinish(src, dst_drive_conf)


def test_diskreplicatefinish_job_not_found():
    src_drive = make_drive(src_drive_conf)
    _vm = FakeVm([src_drive])

    # Passing an empty dict so 'cur' and 'end will not be found
    _vm._dom = FakeDomain({})
    result = _vm.diskReplicateFinish(src_drive_conf, dst_drive_conf)

    assert result == response.error("replicaErr")


def test_diskreplicatefinish_job_not_finished():
    _vm = FakeVm([make_drive(src_drive_conf)])
    _vm._dom = FakeDomain({'cur': 0, 'end': 1})
    result = _vm.diskReplicateFinish(src_drive_conf, dst_drive_conf)

    assert result == response.error("unavail")

    # if pivot was not called the monitor should not have been disabled
    assert not _vm.volume_monitor.was_disabled


def test_blockjobabort_failed(monkeypatch):
    def raising_blockjobabort():
        raise Exception('blockJobAbort failed')

    src_drive = make_drive(src_drive_conf)
    dst_drive = make_drive(dst_drive_conf)

    _vm = FakeVm([src_drive, dst_drive])
    _vm._dom = FakeDomain({'cur': 1, 'end': 1})

    monkeypatch.setattr(FakeDomain, 'blockJobAbort', raising_blockjobabort)
    result = _vm.diskReplicateFinish(src_drive_conf, dst_drive_conf)

    assert result == response.error("changeDisk")


def test_replicatefinish_successful():
    src_drive = make_drive(src_drive_conf)
    dst_drive = make_drive(dst_drive_conf)

    _vm = FakeVm([src_drive, dst_drive])
    _vm._dom = FakeDomain({'cur': 1, 'end': 1})
    _vm.diskReplicateFinish(src_drive_conf, dst_drive_conf)

    # we should have only one device at the end of the replication
    # and its domainID should be the destination's ID
    assert len(_vm._devices) == 1
    assert (_vm._devices[hwclass.DISK][0]['domainID'] ==
            dst_drive_conf['domainID'])

    # we need to check whether the monitor was disabled during the
    # run of diskReplicateFinish
    assert _vm.volume_monitor.was_disabled


def make_drive(drive_conf, shared_type=storage.DRIVE_SHARED_TYPE.EXCLUSIVE):
    drive_conf['shared'] = shared_type
    return storage.Drive(log, **drive_conf)


class FakeVm(vm.Vm):
    def __init__(self, devices=[]):
        self._devices = {hwclass.DISK: devices}
        self.id = "testvm"
        self.volume_monitor = FakeVolumeMonitor()
        self._dom = FakeDomain()
        self.cif = FakeClientIF(log)
        # We don't always pass the destination drive
        if len(devices) > 1:
            self.dst_drive = devices[1]
        else:
            # If dst_drive wasn't passed use the original
            self.dst_drive = {'domainID': 'src-domain-id'}

    def _delDiskReplica(self, drive):
        # as the actual _delDiskReplica does extra stuff like syncing metadata
        # and locking we override it here to make it do only what we care about
        del drive.diskReplicate

    def updateDriveParameters(self, driveParams):
        # We only care about the domainID for the tests
        for vmDrive in self._devices[hwclass.DISK][:]:
            setattr(vmDrive, 'domainID', self.dst_drive['domainID'])


class FakeDomain(object):

    def __init__(self, block_job_info={}):
        self.block_job_info = block_job_info

    def blockJobInfo(self, drive_name, flags=0):
        return self.block_job_info

    def blockJobAbort(self, drive_name, flags=0):
        pass


class FakeClientIF(clientIF):

    def __init__(self, log):
        self.log = log


class FakeVolumeMonitor(object):

    def __init__(self):
        self.enabled = False
        self.was_disabled = False

    def disable(self):
        self.was_disabled = True
        self.enabled = False

    def enable(self):
        self.enabled = True
