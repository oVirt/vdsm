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
from vdsm.virt.vmdevices.storage import Drive, BLOCK_THRESHOLD

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


def test_monitoring_needed():

    class FakeDrive:

        def __init__(self, flag):
            self.flag = flag

        def needs_monitoring(self):
            return self.flag

    vm = FakeVM()
    mon = thinp.VolumeMonitor(vm, vm.log)
    assert not mon.monitoring_needed()

    vm.drives.append(FakeDrive(False))
    assert not mon.monitoring_needed()

    vm.drives.append(FakeDrive(True))
    assert mon.monitoring_needed()

    vm.drives.append(FakeDrive(False))
    assert mon.monitoring_needed()

    mon.disable()
    assert not mon.monitoring_needed()

    mon.enable()
    assert mon.monitoring_needed()

    vm.drives[1].flag = False
    assert not mon.monitoring_needed()


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
