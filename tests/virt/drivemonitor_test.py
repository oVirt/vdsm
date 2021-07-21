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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA
# 02110-1301  USA
#
# Refer to the README and COPYING files for full details of the license
#

import logging

from vdsm.common.units import MiB, GiB
from vdsm.virt.vmdevices import storage
from vdsm.virt import drivemonitor

import pytest


_DISK_DATA = [

    pytest.param(
        [
            {
                'diskType': storage.DISK_TYPE.FILE,
                'format': 'cow',
                'threshold_state': storage.BLOCK_THRESHOLD.UNSET,
                'index': 0,
            },
            {
                'diskType': storage.DISK_TYPE.BLOCK,
                'format': 'raw',
                'threshold_state': storage.BLOCK_THRESHOLD.UNSET,
                'index': 1,
            }
        ],
        [],
        id="non_chunk_drives",
    ),

    pytest.param(
        [
            {
                'diskType': storage.DISK_TYPE.BLOCK,
                'format': 'cow',
                'threshold_state': storage.BLOCK_THRESHOLD.UNSET,
                'index': 0,
            },
            {
                'diskType': storage.DISK_TYPE.BLOCK,
                'format': 'cow',
                'threshold_state': storage.BLOCK_THRESHOLD.SET,
                'index': 1,
            },
            {
                'diskType': storage.DISK_TYPE.BLOCK,
                'format': 'cow',
                'threshold_state': storage.BLOCK_THRESHOLD.EXCEEDED,
                'index': 2,
            }
        ],
        ['vda', 'vdc'],
        id="chunked_drives",
    ),

    pytest.param(
        [
            {
                'diskType': storage.DISK_TYPE.NETWORK,
                'format': 'cow',
                'threshold_state': storage.BLOCK_THRESHOLD.UNSET,
                'index': 0,
            },
            {
                'diskType': storage.DISK_TYPE.NETWORK,
                'format': 'cow',
                'threshold_state': storage.BLOCK_THRESHOLD.SET,
                'index': 1,
            },
            {
                'diskType': storage.DISK_TYPE.NETWORK,
                'format': 'cow',
                'threshold_state': storage.BLOCK_THRESHOLD.EXCEEDED,
                'index': 2,
            }
        ],
        [],
        id="network_drives",
    ),

    pytest.param(
        [
            {
                'diskType': storage.DISK_TYPE.FILE,
                'format': 'cow',
                'threshold_state': storage.BLOCK_THRESHOLD.UNSET,
                'index': 0,
                'diskReplicate': {
                    'format': 'cow',
                    'diskType': storage.DISK_TYPE.BLOCK
                },
            },
            {
                'diskType': storage.DISK_TYPE.FILE,
                'format': 'cow',
                'threshold_state': storage.BLOCK_THRESHOLD.SET,
                'index': 1,
                'diskReplicate': {
                    'format': 'cow',
                    'diskType': storage.DISK_TYPE.BLOCK
                },
            },
            {
                'diskType': storage.DISK_TYPE.FILE,
                'format': 'cow',
                'threshold_state': storage.BLOCK_THRESHOLD.EXCEEDED,
                'index': 2,
                'diskReplicate': {
                    'format': 'cow',
                    'diskType': storage.DISK_TYPE.BLOCK
                },
            }
        ],
        ['vda', 'vdc'],
        id="replicate_file_to_block",
    ),

    pytest.param(
        [
            {
                'diskType': storage.DISK_TYPE.BLOCK,
                'format': 'cow',
                'threshold_state': storage.BLOCK_THRESHOLD.UNSET,
                'index': 0,
                'diskReplicate': {
                    'format': 'cow',
                    'diskType': storage.DISK_TYPE.BLOCK
                },
            },
            {
                'diskType': storage.DISK_TYPE.BLOCK,
                'format': 'cow',
                'threshold_state': storage.BLOCK_THRESHOLD.SET,
                'index': 1,
                'diskReplicate': {
                    'format': 'cow',
                    'diskType': storage.DISK_TYPE.BLOCK
                },
            },
            {
                'diskType': storage.DISK_TYPE.BLOCK,
                'format': 'cow',
                'threshold_state': storage.BLOCK_THRESHOLD.EXCEEDED,
                'index': 2,
                'diskReplicate': {
                    'format': 'cow',
                    'diskType': storage.DISK_TYPE.BLOCK
                },
            }
        ],
        ['vda', 'vdc'],
        id="replicate_block_to_block",
    ),

    pytest.param(
        [
            {
                'diskType': storage.DISK_TYPE.NETWORK,
                'format': 'cow',
                'threshold_state': storage.BLOCK_THRESHOLD.UNSET,
                'index': 0,
                'diskReplicate': {
                    'format': 'cow',
                    'diskType': storage.DISK_TYPE.BLOCK
                },
            },
            {
                'diskType': storage.DISK_TYPE.NETWORK,
                'format': 'cow',
                'threshold_state': storage.BLOCK_THRESHOLD.SET,
                'index': 1,
                'diskReplicate': {
                    'format': 'cow',
                    'diskType': storage.DISK_TYPE.BLOCK
                },
            },
            {
                'diskType': storage.DISK_TYPE.NETWORK,
                'format': 'cow',
                'threshold_state': storage.BLOCK_THRESHOLD.EXCEEDED,
                'index': 2,
                'diskReplicate': {
                    'format': 'cow',
                    'diskType': storage.DISK_TYPE.BLOCK
                },
            }
        ],
        ['vda', 'vdc'],
        id="replicate_network_to_block",
    ),

    pytest.param(
        [
            {
                'diskType': storage.DISK_TYPE.FILE,
                'format': 'cow',
                'threshold_state': storage.BLOCK_THRESHOLD.UNSET,
                'index': 0,
                'diskReplicate': {
                    'diskType': storage.DISK_TYPE.FILE
                },
            }
        ],
        [],
        id="replicate_file_to_file",
    ),

    pytest.param(
        [
            {
                'diskType': storage.DISK_TYPE.BLOCK,
                'format': 'cow',
                'threshold_state': storage.BLOCK_THRESHOLD.UNSET,
                'index': 0,
                'diskReplicate': {
                    'diskType': storage.DISK_TYPE.FILE
                },
            },
            {
                'diskType': storage.DISK_TYPE.BLOCK,
                'format': 'cow',
                'threshold_state': storage.BLOCK_THRESHOLD.SET,
                'index': 1,
                'diskReplicate': {
                    'diskType': storage.DISK_TYPE.FILE
                },
            },
            {
                'diskType': storage.DISK_TYPE.BLOCK,
                'format': 'cow',
                'threshold_state': storage.BLOCK_THRESHOLD.EXCEEDED,
                'index': 2,
                'diskReplicate': {
                    'diskType': storage.DISK_TYPE.FILE
                },
            }
        ],
        ['vda', 'vdc'],
        id="replicate_block_to_file",
    ),

    pytest.param(
        [
            {
                'diskType': storage.DISK_TYPE.NETWORK,
                'format': 'cow',
                'threshold_state': storage.BLOCK_THRESHOLD.UNSET,
                'index': 0,
                'diskReplicate': {
                    'diskType': storage.DISK_TYPE.FILE
                },
            }
        ],
        [],
        id="replicte_network_to_file",
    ),
]

_MONITORABLE_DISK_DATA = [

    pytest.param(
        [
            {
                'diskType': storage.DISK_TYPE.BLOCK,
                'format': 'cow',
                'threshold_state': storage.BLOCK_THRESHOLD.UNSET,
                'index': 0,
                'monitorable': True,
            },
            {
                'diskType': storage.DISK_TYPE.BLOCK,
                'format': 'cow',
                'threshold_state': storage.BLOCK_THRESHOLD.UNSET,
                'index': 1,
                'monitorable': True,
            }
        ],
        ['vda', 'vdb'],
        id="both_drives_enabled",
    ),

    pytest.param(
        [
            {
                'diskType': storage.DISK_TYPE.BLOCK,
                'format': 'cow',
                'threshold_state': storage.BLOCK_THRESHOLD.UNSET,
                'index': 0,
                'monitorable': False,
            },
            {
                'diskType': storage.DISK_TYPE.BLOCK,
                'format': 'cow',
                'threshold_state': storage.BLOCK_THRESHOLD.UNSET,
                'index': 1,
                'monitorable': True,
            }
        ],
        ['vdb'],
        id="first_drive_disabled",
    ),

    pytest.param(
        [
            {

                'diskType': storage.DISK_TYPE.BLOCK,
                'format': 'cow',
                'threshold_state': storage.BLOCK_THRESHOLD.UNSET,
                'index': 0,
                'monitorable': True,
            },
            {
                'diskType': storage.DISK_TYPE.BLOCK,
                'format': 'cow',
                'threshold_state': storage.BLOCK_THRESHOLD.UNSET,
                'index': 1,
                'monitorable': False,
            }
        ],
        ['vda'],
        id="second_drive_disabled",
    ),

    pytest.param(
        [
            {
                'diskType': storage.DISK_TYPE.BLOCK,
                'format': 'cow',
                'threshold_state': storage.BLOCK_THRESHOLD.UNSET,
                'index': 0,
                'monitorable': False,
            },
            {
                'diskType': storage.DISK_TYPE.BLOCK,
                'format': 'cow',
                'threshold_state': storage.BLOCK_THRESHOLD.UNSET,
                'index': 1,
                'monitorable': False,
            }
        ],
        [],
        id="both_drives_disabled",
    ),
]


class TestDrivemonitor:

    @pytest.mark.parametrize("enabled", [True, False])
    def test_enable_on_create(self, enabled):
        vm = FakeVM()
        mon = drivemonitor.DriveMonitor(vm, vm.log, enabled=enabled)
        assert mon.enabled() == enabled

    def test_enable_runtime(self):
        vm = FakeVM()
        mon = drivemonitor.DriveMonitor(vm, vm.log, enabled=False)
        mon.enable()
        assert mon.enabled() is True

    def test_disable_runtime(self):
        vm = FakeVM()
        mon = drivemonitor.DriveMonitor(vm, vm.log, enabled=True)
        mon.disable()
        assert mon.enabled() is False

    def test_set_threshold_drive_name(self):
        vm = FakeVM()
        mon = drivemonitor.DriveMonitor(vm, vm.log)
        vda = make_drive(vm.log, index=0, iface='virtio')
        vm.drives.append(vda)

        apparentsize = 4 * GiB
        threshold = 512 * MiB

        mon.set_threshold(vda, apparentsize)
        expected = apparentsize - threshold
        assert vm._dom.thresholds == [('vda', expected)]

    def test_set_threshold_indexed_name(self):
        vm = FakeVM()
        mon = drivemonitor.DriveMonitor(vm, vm.log)
        vda = make_drive(vm.log, index=0, iface='virtio')
        vm.drives.append(vda)

        apparentsize = 4 * GiB
        threshold = 512 * MiB

        mon.set_threshold(vda, apparentsize, index=1)
        expected = apparentsize - threshold
        assert vm._dom.thresholds == [('vda[1]', expected)]

    def test_set_threshold_drive_too_small(self):
        # We seen the storage subsystem creating drive too small,
        # less than the minimum supported size, 1GiB.
        # While this is a storage issue, the drive monitor should
        # be fixed no never set negative thresholds.
        vm = FakeVM()
        mon = drivemonitor.DriveMonitor(vm, vm.log)
        vda = make_drive(vm.log, index=0, iface='virtio')
        vm.drives.append(vda)

        apparentsize = 128 * MiB

        mon.set_threshold(vda, apparentsize, index=3)
        target, value = vm._dom.thresholds[0]
        assert target == 'vda[3]'
        assert value >= 1

    def test_clear_with_index_equal_none(self):
        vm = FakeVM()
        mon = drivemonitor.DriveMonitor(vm, vm.log)
        vda = make_drive(vm.log, index=0, iface='virtio')

        mon.clear_threshold(vda)
        assert vm._dom.thresholds == [('vda', 0)]

    def test_clear_with_index(self):
        vm = FakeVM()
        mon = drivemonitor.DriveMonitor(vm, vm.log)
        # one drive (virtio, 0)
        vda = make_drive(vm.log, index=0, iface='virtio')

        # clear the 1st element in the backing chain of the drive
        mon.clear_threshold(vda, index=1)
        assert vm._dom.thresholds == [('vda[1]', 0)]

    def test_on_block_threshold_drive_name_ignored(self):
        vm = FakeVM()
        mon = drivemonitor.DriveMonitor(vm, vm.log)
        vda = make_drive(vm.log, index=0, iface='virtio')
        vm.drives.append(vda)

        mon.on_block_threshold("vda", vda.path, 512 * MiB, 10 * MiB)
        assert vda.threshold_state == storage.BLOCK_THRESHOLD.UNSET

    def test_on_block_threshold_indexed_name_handled(self):
        vm = FakeVM()
        mon = drivemonitor.DriveMonitor(vm, vm.log)
        vda = make_drive(vm.log, index=0, iface='virtio')
        vm.drives.append(vda)

        mon.on_block_threshold("vda[1]", vda.path, 512 * MiB, 10 * MiB)
        assert vda.threshold_state == storage.BLOCK_THRESHOLD.EXCEEDED

    def test_on_block_threshold_unknown_drive(self):
        vm = FakeVM()
        mon = drivemonitor.DriveMonitor(vm, vm.log)
        vda = make_drive(vm.log, index=0, iface='virtio')
        vm.drives.append(vda)

        mon.on_block_threshold("vdb", "/unkown/path", 512 * MiB, 10 * MiB)
        assert vda.threshold_state == storage.BLOCK_THRESHOLD.UNSET

    @pytest.mark.parametrize("disks,monitored", _DISK_DATA)
    def test_monitored_drives(self, disks, monitored):
        vm = FakeVM()
        mon = drivemonitor.DriveMonitor(vm, vm.log)
        self._check_monitored_drives(mon, vm, disks, monitored)

    @pytest.mark.parametrize("disks,monitored", _MONITORABLE_DISK_DATA)
    def test_monitored_drives_flag_disabled(self, disks, monitored):
        vm = FakeVM()
        mon = drivemonitor.DriveMonitor(vm, vm.log)
        self._check_monitored_drives(mon, vm, disks, monitored)

    def _check_monitored_drives(self, mon, vm, disks, monitored):
        for conf in disks:
            drive = make_drive(vm.log, **conf)
            drive.threshold_state = conf.get('threshold_state',
                                             storage.BLOCK_THRESHOLD.UNSET)
            drive.monitorable = conf.get('monitorable', True)
            vm.drives.append(drive)
        found = [drv.name for drv in mon.monitored_drives()]
        assert found == monitored


class FakeVM(object):

    log = logging.getLogger('test')

    def __init__(self):
        self.id = "fake-vm-id"
        self.drives = []
        self._dom = FakeDomain()

    def getDiskDevices(self):
        return self.drives[:]


class FakeDomain(object):
    def __init__(self):
        self.thresholds = []

    def setBlockThreshold(self, drive_name, threshold):
        self.thresholds.append((drive_name, threshold))


def make_drive(log, index, **param_dict):
    conf = drive_config(
        index=str(index),
        domainID='domain_%s' % index,
        poolID='pool_%s' % index,
        imageID='image_%s' % index,
        volumeID='volume_%s' % index,
        **param_dict
    )
    return storage.Drive(log, **conf)


def drive_config(**kw):
    """ Return drive configuration updated from **kw """
    conf = {
        'device': 'disk',
        'format': 'cow',
        'iface': 'virtio',
        'index': '0',
        'path': '/path/to/volume',
        'propagateErrors': 'off',
        'shared': 'none',
        'type': 'disk',
        'readonly': False,
    }
    conf.update(kw)
    return conf
