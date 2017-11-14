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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA
# 02110-1301  USA
#
# Refer to the README and COPYING files for full details of the license
#
from __future__ import absolute_import

import logging

from vdsm.virt.vmdevices import storage
from vdsm.virt import drivemonitor

from monkeypatch import MonkeyPatchScope

from testlib import make_config
from testlib import VdsmTestCase
from testlib import expandPermutations, permutations


MB = 1024 ** 2
GB = 1024 ** 3


def make_env(events_enabled):
    vm = FakeVM()
    vm._dom = FakeDomain()

    cfg = make_config([
        ('irs', 'enable_block_threshold_event',
            'true' if events_enabled else 'false')])
    with MonkeyPatchScope([(drivemonitor, 'config', cfg)]):
        mon = drivemonitor.DriveMonitor(vm, vm.log)
    return mon, vm


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
        self.assertEqual(mon.enabled(), enabled)

    def test_enable_runtime(self):
        vm = FakeVM()
        mon = drivemonitor.DriveMonitor(vm, vm.log, enabled=False)
        mon.enable()
        self.assertEqual(mon.enabled(), True)

    def test_disable_runtime(self):
        vm = FakeVM()
        mon = drivemonitor.DriveMonitor(vm, vm.log, enabled=True)
        mon.disable()
        self.assertEqual(mon.enabled(), False)

    def test_set_threshold(self):
        mon, vm = make_env(events_enabled=True)
        vda = make_drive(self.log, index=0, iface='virtio')
        vm.drives.append(vda)

        apparentsize = 4 * GB
        threshold = 512 * MB

        mon.set_threshold(vda, apparentsize)
        expected = apparentsize - threshold
        self.assertEqual(vm._dom.thresholds, [('vda', expected)])

    def test_clear_with_index_equal_none(self):
        mon, vm = make_env(events_enabled=True)
        vda = make_drive(self.log, index=0, iface='virtio')

        mon.clear_threshold(vda)
        self.assertEqual(vm._dom.thresholds, [('vda', 0)])

    def test_clear_with_index(self):
        mon, vm = make_env(events_enabled=True)
        # one drive (virtio, 0)
        vda = make_drive(self.log, index=0, iface='virtio')

        # clear the 1st element in the backing chain of the drive
        mon.clear_threshold(vda, index=1)
        self.assertEqual(vm._dom.thresholds, [('vda[1]', 0)])

    @permutations(_DISK_DATA)
    def test_monitored_drives_with_events(self, disk_confs, expected, _):
        mon, vm = make_env(events_enabled=True)
        self._check_monitored_drives(mon, vm, disk_confs, expected)

    @permutations(_DISK_DATA)
    def test_monitored_drives_without_events(self, disk_confs, _, expected):
        mon, vm = make_env(events_enabled=False)
        self._check_monitored_drives(mon, vm, disk_confs, expected)

    @permutations(_MONITORABLE_DISK_DATA)
    def test_monitored_drives_flag_disabled_with_events(
            self, disk_confs, expected):
        mon, vm = make_env(events_enabled=True)
        self._check_monitored_drives(mon, vm, disk_confs, expected)

    @permutations(_MONITORABLE_DISK_DATA)
    def test_monitored_drives_flag_disabled_without_events(
            self, disk_confs, expected):
        mon, vm = make_env(events_enabled=False)
        self._check_monitored_drives(mon, vm, disk_confs, expected)

    def _check_monitored_drives(self, mon, vm, disk_confs, expected):
        for conf in disk_confs:
            drive = make_drive(self.log, **conf)
            drive.threshold_state = conf.get('threshold_state',
                                             storage.BLOCK_THRESHOLD.UNSET)
            drive.monitorable = conf.get('monitorable', True)
            vm.drives.append(drive)
        found = [drv.name for drv in mon.monitored_drives()]
        self.assertEqual(found, expected)


class FakeVM(object):

    log = logging.getLogger('test')

    def __init__(self):
        self.drives = []

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
