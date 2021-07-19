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

from contextlib import contextmanager
import logging

from vdsm.common.units import MiB, GiB
from vdsm.virt.vmdevices import storage
from vdsm.virt import drivemonitor

from monkeypatch import MonkeyPatchScope

from testlib import make_config
from testlib import VdsmTestCase
from testlib import expandPermutations, permutations


@contextmanager
def make_env(events_enabled):
    vm = FakeVM()

    cfg = make_config([
        ('irs', 'enable_block_threshold_event',
            'true' if events_enabled else 'false')])
    with MonkeyPatchScope([(drivemonitor, 'config', cfg)]):
        mon = drivemonitor.DriveMonitor(vm, vm.log)
        yield mon, vm


# always use the VirtIO interface (iface='virtio'),
# so all the expected drives will be vd?
_DISK_DATA = [
    # disk_confs, expceted_with_events, expected_without_events

    # Non-chunked drives
    ([{
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
      }],
     [], []),

    # Chunked drives
    ([{
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
      }],
     ['vda', 'vdc'], ['vda', 'vdb', 'vdc']),

    # Networked drives
    ([{
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
      }],
     [], []),

    # Replicating file to block
    ([{
      'diskType': storage.DISK_TYPE.FILE,
      'format': 'cow',
      'diskReplicate':
      {
          'format': 'cow',
          'diskType': storage.DISK_TYPE.BLOCK
      },
      'threshold_state': storage.BLOCK_THRESHOLD.UNSET,
      'index': 0,
      },
      {
      'diskType':
      storage.DISK_TYPE.FILE,
      'format': 'cow',
      'diskReplicate':
      {
          'format': 'cow',
          'diskType': storage.DISK_TYPE.BLOCK
      },
      'threshold_state': storage.BLOCK_THRESHOLD.SET,
      'index': 1,
      },
      {
      'diskType':
      storage.DISK_TYPE.FILE,
      'format': 'cow',
      'diskReplicate':
      {
          'format': 'cow',
          'diskType': storage.DISK_TYPE.BLOCK
      },
      'threshold_state': storage.BLOCK_THRESHOLD.EXCEEDED,
      'index': 2,
      }],
     ['vda', 'vdc'], ['vda', 'vdb', 'vdc']),

    # Replicating block to block
    ([{
      'diskType': storage.DISK_TYPE.BLOCK,
      'format': 'cow',
      'diskReplicate':
      {
          'format': 'cow',
          'diskType': storage.DISK_TYPE.BLOCK
      },
      'threshold_state': storage.BLOCK_THRESHOLD.UNSET,
      'index': 0,
      },
      {
      'diskType': storage.DISK_TYPE.BLOCK,
      'format': 'cow',
      'diskReplicate':
      {
          'format': 'cow',
          'diskType': storage.DISK_TYPE.BLOCK
      },
      'threshold_state': storage.BLOCK_THRESHOLD.SET,
      'index': 1,
      },
      {
      'diskType': storage.DISK_TYPE.BLOCK,
      'format': 'cow',
      'diskReplicate':
      {
          'format': 'cow',
          'diskType': storage.DISK_TYPE.BLOCK
      },
      'threshold_state': storage.BLOCK_THRESHOLD.EXCEEDED,
      'index': 2,
      }],
     ['vda', 'vdc'], ['vda', 'vdb', 'vdc']),

    # Replicating network to block
    ([{
      'diskType': storage.DISK_TYPE.NETWORK,
      'format': 'cow',
      'diskReplicate':
      {
          'format': 'cow',
          'diskType': storage.DISK_TYPE.BLOCK
      },
      'threshold_state': storage.BLOCK_THRESHOLD.UNSET,
      'index': 0,
      },
      {
      'diskType': storage.DISK_TYPE.NETWORK,
      'format': 'cow',
      'diskReplicate':
      {
          'format': 'cow',
          'diskType': storage.DISK_TYPE.BLOCK
      },
      'threshold_state': storage.BLOCK_THRESHOLD.SET,
      'index': 1,
      },
      {
      'diskType': storage.DISK_TYPE.NETWORK,
      'format': 'cow',
      'diskReplicate':
      {
          'format': 'cow',
          'diskType': storage.DISK_TYPE.BLOCK
      },
      'threshold_state': storage.BLOCK_THRESHOLD.EXCEEDED,
      'index': 2,
      }],
     ['vda', 'vdc'], ['vda', 'vdb', 'vdc']),

    # Replicating file to file
    ([{
      'diskType': storage.DISK_TYPE.FILE,
      'format': 'cow',
      'diskReplicate':
      {
          'diskType': storage.DISK_TYPE.FILE
      },
      'threshold_state': storage.BLOCK_THRESHOLD.UNSET,
      'index': 0,
      }],
     [], []),

    # Replicating block to file
    ([{
      'diskType': storage.DISK_TYPE.BLOCK,
      'format': 'cow',
      'diskReplicate':
      {
          'diskType': storage.DISK_TYPE.FILE
      },
      'threshold_state': storage.BLOCK_THRESHOLD.UNSET,
      'index': 0,
      },
      {
      'diskType': storage.DISK_TYPE.BLOCK,
      'format': 'cow',
      'diskReplicate':
      {
          'diskType': storage.DISK_TYPE.FILE
      },
      'threshold_state': storage.BLOCK_THRESHOLD.SET,
      'index': 1,
      },
      {
      'diskType': storage.DISK_TYPE.BLOCK,
      'format': 'cow',
      'diskReplicate':
      {
          'diskType': storage.DISK_TYPE.FILE
      },
      'threshold_state': storage.BLOCK_THRESHOLD.EXCEEDED,
      'index': 2,
      }],
     ['vda', 'vdc'], ['vda', 'vdb', 'vdc']),

    # Replicating network to file
    ([{
      'diskType': storage.DISK_TYPE.NETWORK,
      'format': 'cow',
      'diskReplicate':
      {
          'diskType': storage.DISK_TYPE.FILE
      },
      'threshold_state': storage.BLOCK_THRESHOLD.UNSET,
      'index': 0,
      }],
     [], []),
]

_MONITORABLE_DISK_DATA = [

    # Both drives enabled.
    ([{
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
      }],
     ['vda', 'vdb']),

    # First drive disabled.
    ([{
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
      }],
     ['vdb']),

    # Second drive disabled.
    ([{
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
      }],
     ['vda']),

    # Both drives disabled.
    ([{
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
      }],
     []),
]


@expandPermutations
class TestDrivemonitor(VdsmTestCase):

    @permutations([
        # enabled
        (True,),
        (False,),
    ])
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
        with make_env(events_enabled=True) as (mon, vm):
            vda = make_drive(self.log, index=0, iface='virtio')
            vm.drives.append(vda)

            apparentsize = 4 * GiB
            threshold = 512 * MiB

            mon.set_threshold(vda, apparentsize)
            expected = apparentsize - threshold
            assert vm._dom.thresholds == [('vda', expected)]

    def test_set_threshold_indexed_name(self):
        with make_env(events_enabled=True) as (mon, vm):
            vda = make_drive(self.log, index=0, iface='virtio')
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
        with make_env(events_enabled=True) as (mon, vm):
            vda = make_drive(self.log, index=0, iface='virtio')
            vm.drives.append(vda)

            apparentsize = 128 * MiB

            mon.set_threshold(vda, apparentsize, index=3)
            target, value = vm._dom.thresholds[0]
            assert target == 'vda[3]'
            assert value >= 1

    def test_clear_with_index_equal_none(self):
        with make_env(events_enabled=True) as (mon, vm):
            vda = make_drive(self.log, index=0, iface='virtio')

            mon.clear_threshold(vda)
            assert vm._dom.thresholds == [('vda', 0)]

    def test_clear_with_index(self):
        with make_env(events_enabled=True) as (mon, vm):
            # one drive (virtio, 0)
            vda = make_drive(self.log, index=0, iface='virtio')

            # clear the 1st element in the backing chain of the drive
            mon.clear_threshold(vda, index=1)
            assert vm._dom.thresholds == [('vda[1]', 0)]

    def test_clear_with_events_disabled(self):
        with make_env(events_enabled=False) as (mon, vm):
            vda = make_drive(self.log, index=0, iface='virtio')

            mon.clear_threshold(vda)
            assert vm._dom.thresholds == []

    def test_on_block_threshold_drive_name_ignored(self):
        with make_env(events_enabled=True) as (mon, vm):
            vda = make_drive(self.log, index=0, iface='virtio')
            vm.drives.append(vda)

            mon.on_block_threshold("vda", vda.path, 512 * MiB, 10 * MiB)
            assert vda.threshold_state == storage.BLOCK_THRESHOLD.UNSET

    def test_on_block_threshold_indexed_name_handled(self):
        with make_env(events_enabled=True) as (mon, vm):
            vda = make_drive(self.log, index=0, iface='virtio')
            vm.drives.append(vda)

            mon.on_block_threshold("vda[1]", vda.path, 512 * MiB, 10 * MiB)
            assert vda.threshold_state == storage.BLOCK_THRESHOLD.EXCEEDED

    def test_on_block_threshold_unknown_drive(self):
        with make_env(events_enabled=True) as (mon, vm):
            vda = make_drive(self.log, index=0, iface='virtio')
            vm.drives.append(vda)

            mon.on_block_threshold("vdb", "/unkown/path", 512 * MiB, 10 * MiB)
            assert vda.threshold_state == storage.BLOCK_THRESHOLD.UNSET

    @permutations(_DISK_DATA)
    def test_monitored_drives_with_events(self, disk_confs, expected, _):
        with make_env(events_enabled=True) as (mon, vm):
            self._check_monitored_drives(mon, vm, disk_confs, expected)

    @permutations(_DISK_DATA)
    def test_monitored_drives_without_events(self, disk_confs, _, expected):
        with make_env(events_enabled=False) as (mon, vm):
            self._check_monitored_drives(mon, vm, disk_confs, expected)

    @permutations(_MONITORABLE_DISK_DATA)
    def test_monitored_drives_flag_disabled_with_events(
            self, disk_confs, expected):
        with make_env(events_enabled=True) as (mon, vm):
            self._check_monitored_drives(mon, vm, disk_confs, expected)

    @permutations(_MONITORABLE_DISK_DATA)
    def test_monitored_drives_flag_disabled_without_events(
            self, disk_confs, expected):
        with make_env(events_enabled=True) as (mon, vm):
            self._check_monitored_drives(mon, vm, disk_confs, expected)

    def _check_monitored_drives(self, mon, vm, disk_confs, expected):
        for conf in disk_confs:
            drive = make_drive(self.log, **conf)
            drive.threshold_state = conf.get('threshold_state',
                                             storage.BLOCK_THRESHOLD.UNSET)
            drive.monitorable = conf.get('monitorable', True)
            vm.drives.append(drive)
        found = [drv.name for drv in mon.monitored_drives()]
        assert found == expected


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
