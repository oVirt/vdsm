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

from vdsm.virt import thinp

from vdsm.common.units import MiB, GiB
from vdsm.virt.vmdevices.storage import Drive, DISK_TYPE, BLOCK_THRESHOLD

import pytest


@pytest.mark.parametrize("enabled", [True, False])
def test_enable_on_create(enabled):
    vm = FakeVM()
    mon = thinp.VolumeMonitor(vm, vm.log, enabled=enabled)
    assert mon.enabled() == enabled


def test_enable_runtime():
    vm = FakeVM()
    mon = thinp.VolumeMonitor(vm, vm.log, enabled=False)
    mon.enable()
    assert mon.enabled() is True


def test_disable_runtime():
    vm = FakeVM()
    mon = thinp.VolumeMonitor(vm, vm.log, enabled=True)
    mon.disable()
    assert mon.enabled() is False


def test_set_threshold():
    vm = FakeVM()
    mon = thinp.VolumeMonitor(vm, vm.log)
    vda = make_drive(vm.log, index=0, iface='virtio')
    vm.drives.append(vda)

    apparentsize = 4 * GiB
    threshold = 512 * MiB

    # TODO: Use public API.
    mon._set_threshold(vda, apparentsize, 1)
    expected = apparentsize - threshold
    assert vm._dom.thresholds == [('vda[1]', expected)]


def test_set_threshold_drive_too_small():
    # We seen the storage subsystem creating drive too small,
    # less than the minimum supported size, 1GiB.
    # While this is a storage issue, the volume monitor should
    # be fixed no never set negative thresholds.
    vm = FakeVM()
    mon = thinp.VolumeMonitor(vm, vm.log)
    vda = make_drive(vm.log, index=0, iface='virtio')
    vm.drives.append(vda)

    apparentsize = 128 * MiB

    # TODO: Use public API.
    mon._set_threshold(vda, apparentsize, 3)
    target, value = vm._dom.thresholds[0]
    assert target == 'vda[3]'
    assert value >= 1


def test_clear_threshold():
    vm = FakeVM()
    mon = thinp.VolumeMonitor(vm, vm.log)
    # one drive (virtio, 0)
    vda = make_drive(vm.log, index=0, iface='virtio')

    # clear the 1st element in the backing chain of the drive
    mon.clear_threshold(vda, 1)
    assert vm._dom.thresholds == [('vda[1]', 0)]


def test_on_block_threshold_drive_name_ignored():
    vm = FakeVM()
    mon = thinp.VolumeMonitor(vm, vm.log)
    vda = make_drive(vm.log, index=0, iface='virtio')
    vm.drives.append(vda)

    mon.on_block_threshold("vda", vda.path, 512 * MiB, 10 * MiB)
    assert vda.threshold_state == BLOCK_THRESHOLD.UNSET


def test_on_block_threshold_indexed_name_handled():
    vm = FakeVM()
    mon = thinp.VolumeMonitor(vm, vm.log)
    vda = make_drive(vm.log, index=0, iface='virtio')
    vm.drives.append(vda)

    mon.on_block_threshold("vda[1]", vda.path, 512 * MiB, 10 * MiB)
    assert vda.threshold_state == BLOCK_THRESHOLD.EXCEEDED


def test_on_block_threshold_unknown_drive():
    vm = FakeVM()
    mon = thinp.VolumeMonitor(vm, vm.log)
    vda = make_drive(vm.log, index=0, iface='virtio')
    vm.drives.append(vda)

    mon.on_block_threshold("vdb", "/unkown/path", 512 * MiB, 10 * MiB)
    assert vda.threshold_state == BLOCK_THRESHOLD.UNSET


@pytest.mark.parametrize("drives,monitored", [

    pytest.param(
        [
            {
                'diskType': DISK_TYPE.FILE,
                'format': 'cow',
                'threshold_state': BLOCK_THRESHOLD.UNSET,
                'index': 0,
            },
            {
                'diskType': DISK_TYPE.BLOCK,
                'format': 'raw',
                'threshold_state': BLOCK_THRESHOLD.UNSET,
                'index': 1,
            }
        ],
        [],
        id="non_chunk_drives",
    ),

    pytest.param(
        [
            {
                'diskType': DISK_TYPE.BLOCK,
                'format': 'cow',
                'threshold_state': BLOCK_THRESHOLD.UNSET,
                'index': 0,
            },
            {
                'diskType': DISK_TYPE.BLOCK,
                'format': 'cow',
                'threshold_state': BLOCK_THRESHOLD.SET,
                'index': 1,
            },
            {
                'diskType': DISK_TYPE.BLOCK,
                'format': 'cow',
                'threshold_state': BLOCK_THRESHOLD.EXCEEDED,
                'index': 2,
            }
        ],
        ['vda', 'vdc'],
        id="chunked_drives",
    ),

    pytest.param(
        [
            {
                'diskType': DISK_TYPE.NETWORK,
                'format': 'cow',
                'threshold_state': BLOCK_THRESHOLD.UNSET,
                'index': 0,
            },
            {
                'diskType': DISK_TYPE.NETWORK,
                'format': 'cow',
                'threshold_state': BLOCK_THRESHOLD.SET,
                'index': 1,
            },
            {
                'diskType': DISK_TYPE.NETWORK,
                'format': 'cow',
                'threshold_state': BLOCK_THRESHOLD.EXCEEDED,
                'index': 2,
            }
        ],
        [],
        id="network_drives",
    ),

    pytest.param(
        [
            {
                'diskType': DISK_TYPE.FILE,
                'format': 'cow',
                'threshold_state': BLOCK_THRESHOLD.UNSET,
                'index': 0,
                'diskReplicate': {
                    'format': 'cow',
                    'diskType': DISK_TYPE.BLOCK
                },
            },
            {
                'diskType': DISK_TYPE.FILE,
                'format': 'cow',
                'threshold_state': BLOCK_THRESHOLD.SET,
                'index': 1,
                'diskReplicate': {
                    'format': 'cow',
                    'diskType': DISK_TYPE.BLOCK
                },
            },
            {
                'diskType': DISK_TYPE.FILE,
                'format': 'cow',
                'threshold_state': BLOCK_THRESHOLD.EXCEEDED,
                'index': 2,
                'diskReplicate': {
                    'format': 'cow',
                    'diskType': DISK_TYPE.BLOCK
                },
            }
        ],
        ['vda', 'vdc'],
        id="replicate_file_to_block",
    ),

    pytest.param(
        [
            {
                'diskType': DISK_TYPE.BLOCK,
                'format': 'cow',
                'threshold_state': BLOCK_THRESHOLD.UNSET,
                'index': 0,
                'diskReplicate': {
                    'format': 'cow',
                    'diskType': DISK_TYPE.BLOCK
                },
            },
            {
                'diskType': DISK_TYPE.BLOCK,
                'format': 'cow',
                'threshold_state': BLOCK_THRESHOLD.SET,
                'index': 1,
                'diskReplicate': {
                    'format': 'cow',
                    'diskType': DISK_TYPE.BLOCK
                },
            },
            {
                'diskType': DISK_TYPE.BLOCK,
                'format': 'cow',
                'threshold_state': BLOCK_THRESHOLD.EXCEEDED,
                'index': 2,
                'diskReplicate': {
                    'format': 'cow',
                    'diskType': DISK_TYPE.BLOCK
                },
            }
        ],
        ['vda', 'vdc'],
        id="replicate_block_to_block",
    ),

    pytest.param(
        [
            {
                'diskType': DISK_TYPE.NETWORK,
                'format': 'cow',
                'threshold_state': BLOCK_THRESHOLD.UNSET,
                'index': 0,
                'diskReplicate': {
                    'format': 'cow',
                    'diskType': DISK_TYPE.BLOCK
                },
            },
            {
                'diskType': DISK_TYPE.NETWORK,
                'format': 'cow',
                'threshold_state': BLOCK_THRESHOLD.SET,
                'index': 1,
                'diskReplicate': {
                    'format': 'cow',
                    'diskType': DISK_TYPE.BLOCK
                },
            },
            {
                'diskType': DISK_TYPE.NETWORK,
                'format': 'cow',
                'threshold_state': BLOCK_THRESHOLD.EXCEEDED,
                'index': 2,
                'diskReplicate': {
                    'format': 'cow',
                    'diskType': DISK_TYPE.BLOCK
                },
            }
        ],
        ['vda', 'vdc'],
        id="replicate_network_to_block",
    ),

    pytest.param(
        [
            {
                'diskType': DISK_TYPE.FILE,
                'format': 'cow',
                'threshold_state': BLOCK_THRESHOLD.UNSET,
                'index': 0,
                'diskReplicate': {
                    'diskType': DISK_TYPE.FILE
                },
            }
        ],
        [],
        id="replicate_file_to_file",
    ),

    pytest.param(
        [
            {
                'diskType': DISK_TYPE.BLOCK,
                'format': 'cow',
                'threshold_state': BLOCK_THRESHOLD.UNSET,
                'index': 0,
                'diskReplicate': {
                    'diskType': DISK_TYPE.FILE
                },
            },
            {
                'diskType': DISK_TYPE.BLOCK,
                'format': 'cow',
                'threshold_state': BLOCK_THRESHOLD.SET,
                'index': 1,
                'diskReplicate': {
                    'diskType': DISK_TYPE.FILE
                },
            },
            {
                'diskType': DISK_TYPE.BLOCK,
                'format': 'cow',
                'threshold_state': BLOCK_THRESHOLD.EXCEEDED,
                'index': 2,
                'diskReplicate': {
                    'diskType': DISK_TYPE.FILE
                },
            }
        ],
        ['vda', 'vdc'],
        id="replicate_block_to_file",
    ),

    pytest.param(
        [
            {
                'diskType': DISK_TYPE.NETWORK,
                'format': 'cow',
                'threshold_state': BLOCK_THRESHOLD.UNSET,
                'index': 0,
                'diskReplicate': {
                    'diskType': DISK_TYPE.FILE
                },
            }
        ],
        [],
        id="replicte_network_to_file",
    ),

    pytest.param(
        [
            {
                'diskType': DISK_TYPE.BLOCK,
                'format': 'cow',
                'threshold_state': BLOCK_THRESHOLD.UNSET,
                'index': 0,
                'monitorable': True,
            },
            {
                'diskType': DISK_TYPE.BLOCK,
                'format': 'cow',
                'threshold_state': BLOCK_THRESHOLD.UNSET,
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
                'diskType': DISK_TYPE.BLOCK,
                'format': 'cow',
                'threshold_state': BLOCK_THRESHOLD.UNSET,
                'index': 0,
                'monitorable': False,
            },
            {
                'diskType': DISK_TYPE.BLOCK,
                'format': 'cow',
                'threshold_state': BLOCK_THRESHOLD.UNSET,
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

                'diskType': DISK_TYPE.BLOCK,
                'format': 'cow',
                'threshold_state': BLOCK_THRESHOLD.UNSET,
                'index': 0,
                'monitorable': True,
            },
            {
                'diskType': DISK_TYPE.BLOCK,
                'format': 'cow',
                'threshold_state': BLOCK_THRESHOLD.UNSET,
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
                'diskType': DISK_TYPE.BLOCK,
                'format': 'cow',
                'threshold_state': BLOCK_THRESHOLD.UNSET,
                'index': 0,
                'monitorable': False,
            },
            {
                'diskType': DISK_TYPE.BLOCK,
                'format': 'cow',
                'threshold_state': BLOCK_THRESHOLD.UNSET,
                'index': 1,
                'monitorable': False,
            }
        ],
        [],
        id="both_drives_disabled",
    ),
])
def test_monitored_volumes(drives, monitored):
    vm = FakeVM()
    mon = thinp.VolumeMonitor(vm, vm.log)
    for conf in drives:
        drive = make_drive(vm.log, **conf)
        drive.threshold_state = conf.get('threshold_state',
                                         BLOCK_THRESHOLD.UNSET)
        drive.monitorable = conf.get('monitorable', True)
        vm.drives.append(drive)

    # TODO: Use public API
    found = [drv.name for drv in mon._monitored_volumes()]
    assert found == monitored


def test_query_block_stats():
    vm = FakeVM()

    vm.block_stats = {
        # Empty cdrom
        "block.count": 4,
        "block.0.name": "sdc",
        "block.0.rd.reqs": 19,
        "block.0.rd.bytes": 410,
        "block.0.rd.times": 79869,
        "block.0.wr.reqs": 0,
        "block.0.wr.bytes": 0,
        "block.0.wr.times": 0,
        "block.0.fl.reqs": 0,
        "block.0.fl.times": 0,

        # 6g qcow2 active layer that was extended many times.
        "block.1.name": "sda",
        "block.1.path": "/rhev/.../44d498a1-54a5-4371-8eda-02d839d7c840",
        "block.1.backingIndex": 2,
        "block.1.rd.reqs": 13448,
        "block.1.rd.bytes": 415614976,
        "block.1.rd.times": 9940902315,
        "block.1.wr.reqs": 4909,
        "block.1.wr.bytes": 82999296,
        "block.1.wr.times": 47469574949,
        "block.1.fl.reqs": 683,
        "block.1.fl.times": 4204366339,
        "block.1.allocation": 216006656,
        "block.1.capacity": 6442450944,
        "block.1.physical": 7113539584,
        "block.1.threshold": 6576668672,

        # 6g qcow2 backing file.
        "block.2.name": "sda",
        "block.2.path": "/rhev/.../9d63f782-7467-4243-af1e-5c1f8b49c111",
        "block.2.backingIndex": 4,
        "block.2.allocation": 0,
        "block.2.capacity": 6442450944,
        "block.2.physical": 3087007744,

        # 20g Data disk active layer.
        "block.3.name": "sdd",
        "block.3.path": "/rhev/.../cf6552e0-1c88-4b2a-aec6-0d2f26c2aaea",
        "block.3.backingIndex": 7,
        "block.3.rd.reqs": 50,
        "block.3.rd.bytes": 1077248,
        "block.3.rd.times": 1200296,
        "block.3.wr.reqs": 0,
        "block.3.wr.bytes": 0,
        "block.3.wr.times": 0,
        "block.3.fl.reqs": 0,
        "block.3.fl.times": 0,
        "block.3.allocation": 0,
        "block.3.capacity": 21474836480,
        "block.3.physical": 1073741824,
        "block.3.threshold": 536870912
    }

    mon = thinp.VolumeMonitor(vm, vm.log)
    block_stats = mon._query_block_stats()

    assert block_stats == {
        2: thinp.BlockInfo(
            index=2,
            name='sda',
            path='/rhev/.../44d498a1-54a5-4371-8eda-02d839d7c840',
            allocation=216006656,
            capacity=6442450944,
            physical=7113539584,
            threshold=6576668672,
        ),
        4: thinp.BlockInfo(
            index=4,
            name='sda',
            path='/rhev/.../9d63f782-7467-4243-af1e-5c1f8b49c111',
            allocation=0,
            capacity=6442450944,
            physical=3087007744,
            threshold=0,
        ),
        7: thinp.BlockInfo(
            index=7,
            name='sdd',
            path='/rhev/.../cf6552e0-1c88-4b2a-aec6-0d2f26c2aaea',
            allocation=0,
            capacity=21474836480,
            physical=1073741824,
            threshold=536870912,
        ),
    }


class FakeVM(object):

    log = logging.getLogger('test')

    def __init__(self):
        self.id = "fake-vm-id"
        self.drives = []
        self.block_stats = []
        self._dom = FakeDomain()

    def getDiskDevices(self):
        return self.drives[:]

    def query_block_stats(self):
        return self.block_stats


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
    return Drive(log, **conf)


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
