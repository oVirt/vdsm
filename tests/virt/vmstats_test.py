#
# Copyright 2015-2020 Red Hat, Inc.
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

import copy
import logging
import uuid

import six

from vdsm.common.units import KiB, MiB, GiB
from vdsm.virt import vmstats

from fakelib import FakeLogger
from testlib import VdsmTestCase as TestCaseBase
from testlib import permutations, expandPermutations
from monkeypatch import MonkeyPatchScope


_FAKE_BULK_STATS = {
    'f3243a90-2e9e-4061-b7b3-a6c585e14857': (
        {
            'state.state': 1,
            'state.reason': 1,
            'cpu.time': 13755069120,
            'cpu.user': 3370000000,
            'cpu.system': 6320000000,
            'balloon.current': 4194304,
            'balloon.maximum': 4194304,
            'vcpu.current': 2,
            'vcpu.maximum': 16,
            'vcpu.0.state': 1,
            'vcpu.0.time': 10910000000,
            'vcpu.1.state': 1,
            'vcpu.1.time': 0,
            'net.count': 1,
            'net.0.name': 'vnet0',
            'net.0.rx.bytes': 0,
            'net.0.rx.pkts': 0,
            'net.0.rx.errs': 0,
            'net.0.rx.drop': 0,
            'net.0.tx.bytes': 0,
            'net.0.tx.pkts': 0,
            'net.0.tx.errs': 0,
            'net.0.tx.drop': 0,
            'block.count': 2,
            'block.0.name': 'hdc',
            'block.0.rd.reqs': 0,
            'block.0.rd.bytes': 0,
            'block.0.rd.times': 0,
            'block.0.wr.reqs': 0,
            'block.0.wr.bytes': 0,
            'block.0.wr.times': 0,
            'block.0.fl.reqs': 0,
            'block.0.fl.times': 0,
            'block.0.allocation': 0,
            'block.1.name': 'vda',
            'block.1.path': (
                '/rhev'
                '/data-center'
                '/00000001-0001-0001-0001-0000000001e8'
                '/bbed5784-b0ee-4a0a-aff2-801da0bcf39e'
                '/images'
                '/cbe82d1f-a0ba-4af2-af2f-788d15eef043'
                '/7ba49d31-4fa7-49df-8df4-37a22de79f62'
            ),
            'block.1.rd.reqs': 1,
            'block.1.rd.bytes': 512,
            'block.1.rd.times': 58991,
            'block.1.wr.reqs': 0,
            'block.1.wr.bytes': 0,
            'block.1.wr.times': 0,
            'block.1.fl.reqs': 0,
            'block.1.fl.times': 0,
            'block.1.allocation': 0,
            'block.1.capacity': 42949672960,
        },
        {
            'state.state': 1,
            'state.reason': 1,
            'cpu.time': 13755069120,
            'cpu.user': 3370000000,
            'cpu.system': 6320000000,
            'balloon.current': 4194304,
            'balloon.maximum': 4194304,
            'vcpu.current': 2,
            'vcpu.maximum': 16,
            'vcpu.0.state': 1,
            'vcpu.0.time': 10910000000,
            'vcpu.1.state': 1,
            'vcpu.1.time': 0,
            'net.count': 2,
            'net.0.name': 'vnet1',
            'net.0.rx.bytes': 0,
            'net.0.rx.pkts': 0,
            'net.0.rx.errs': 0,
            'net.0.rx.drop': 0,
            'net.0.tx.bytes': 0,
            'net.0.tx.pkts': 0,
            'net.0.tx.errs': 0,
            'net.0.tx.drop': 0,
            'net.1.name': 'vnet0',
            'net.1.rx.bytes': 1024,
            'net.1.rx.pkts': 128,
            'net.1.rx.errs': 0,
            'net.1.rx.drop': 0,
            'net.1.tx.bytes': 2048,
            'net.1.tx.pkts': 256,
            'net.1.tx.errs': 0,
            'net.1.tx.drop': 0,
            'block.count': 3,
            'block.0.name': 'hdd',
            'block.0.rd.reqs': 0,
            'block.0.rd.bytes': 0,
            'block.0.rd.times': 0,
            'block.0.wr.reqs': 0,
            'block.0.wr.bytes': 0,
            'block.0.wr.times': 0,
            'block.0.fl.reqs': 0,
            'block.0.fl.times': 0,
            'block.0.allocation': 0,
            'block.1.name': 'vda',
            'block.1.path': (
                '/rhev'
                '/data-center'
                '/00000001-0001-0001-0001-0000000001e8'
                '/bbed5784-b0ee-4a0a-aff2-801da0bcf39e'
                '/images'
                '/cbe82d1f-a0ba-4af2-af2f-788d15eef043'
                '/7ba49d31-4fa7-49df-8df4-37a22de79f62'
            ),
            'block.1.rd.reqs': 1,
            'block.1.rd.bytes': 512,
            'block.1.rd.times': 58991,
            'block.1.wr.reqs': 0,
            'block.1.wr.bytes': 0,
            'block.1.wr.times': 0,
            'block.1.fl.reqs': 0,
            'block.1.fl.times': 0,
            'block.1.allocation': 0,
            'block.1.capacity': 42949672960,
            'block.2.name': 'hdc',
            'block.2.rd.reqs': 0,
            'block.2.rd.bytes': 0,
            'block.2.rd.times': 0,
            'block.2.wr.reqs': 0,
            'block.2.wr.bytes': 0,
            'block.2.wr.times': 0,
            'block.2.fl.reqs': 0,
            'block.2.fl.times': 0,
            'block.2.allocation': 0,
        },
    ),
}

# on SR-IOV we seen unexpected net.count == 2 but data only for one nic.
_FAKE_BULK_STATS_SRIOV = {
    'f3243a90-2e9e-4061-b7b3-a6c585e14857': (
        {
            'state.state': 1,
            'state.reason': 1,
            'cpu.time': 13755069120,
            'cpu.user': 3370000000,
            'cpu.system': 6320000000,
            'balloon.current': 4194304,
            'balloon.maximum': 4194304,
            'vcpu.current': 2,
            'vcpu.maximum': 16,
            'vcpu.0.state': 1,
            'vcpu.0.time': 10910000000,
            'vcpu.1.state': 1,
            'vcpu.1.time': 0,
            'net.count': 2,
            'net.1.name': 'vnet1',
            'net.1.rx.bytes': 0,
            'net.1.rx.pkts': 0,
            'net.1.rx.errs': 0,
            'net.1.rx.drop': 0,
            'net.1.tx.bytes': 0,
            'net.1.tx.pkts': 0,
            'net.1.tx.errs': 0,
            'net.1.tx.drop': 0,
            'block.count': 2,
            'block.0.name': 'hdc',
            'block.0.rd.reqs': 0,
            'block.0.rd.bytes': 0,
            'block.0.rd.times': 0,
            'block.0.wr.reqs': 0,
            'block.0.wr.bytes': 0,
            'block.0.wr.times': 0,
            'block.0.fl.reqs': 0,
            'block.0.fl.times': 0,
            'block.0.allocation': 0,
            'block.1.name': 'vda',
            'block.1.path': (
                '/rhev'
                '/data-center'
                '/00000001-0001-0001-0001-0000000001e8'
                '/bbed5784-b0ee-4a0a-aff2-801da0bcf39e'
                '/images'
                '/cbe82d1f-a0ba-4af2-af2f-788d15eef043'
                '/7ba49d31-4fa7-49df-8df4-37a22de79f62'
            ),
            'block.1.rd.reqs': 1,
            'block.1.rd.bytes': 512,
            'block.1.rd.times': 58991,
            'block.1.wr.reqs': 0,
            'block.1.wr.bytes': 0,
            'block.1.wr.times': 0,
            'block.1.fl.reqs': 0,
            'block.1.fl.times': 0,
            'block.1.allocation': 0,
            'block.1.capacity': 42949672960,
        },
    )
}


class VmStatsTestCase(TestCaseBase):

    def setUp(self):
        # just pick one sampling
        self.samples = next(six.itervalues(_FAKE_BULK_STATS))
        self.bulk_stats = self.samples[0]
        self.interval = 10  # seconds

    def assertNameIsAt(self, stats, group, idx, name):
        assert stats['%s.%d.name' % (group, idx)] == name

    def assertStatsHaveKeys(self, stats, keys):
        for key in keys:
            assert key in stats

    def assertRepeatedStatsHaveKeys(self, items, stats, keys):
        for item in items:
            self.assertStatsHaveKeys(stats[item.name], keys)


@expandPermutations
class UtilsFunctionsTests(VmStatsTestCase):

    # we should not test private functions, but this one is
    # the cornerstone of bulk stats translation, so we make
    # one exception for the sake of the practicality.

    @permutations([['block', 'hdc'], ['net', 'vnet0']])
    def test_find_existing(self, group, name):
        indexes = vmstats._find_bulk_stats_reverse_map(
            self.bulk_stats, group)
        self.assertNameIsAt(
            self.bulk_stats, group, indexes[name], name)

    @permutations([['block'], ['net']])
    def test_find_bogus(self, group):
        name = 'inexistent'
        indexes = vmstats._find_bulk_stats_reverse_map(
            self.bulk_stats, group)
        assert name not in indexes

    @permutations([['block', 'hdc'], ['net', 'vnet0']])
    def test_index_can_change(self, group, name):
        all_indexes = []

        for bulk_stats in self.samples:
            indexes = vmstats._find_bulk_stats_reverse_map(
                bulk_stats, group)

            self.assertNameIsAt(bulk_stats, group, indexes[name], name)
            all_indexes.append(indexes)

        # and indeed indexes must change
        assert len(all_indexes) == len(self.samples)

    def test_network_missing(self):
        # seen using SR-IOV

        bulk_stats = next(six.itervalues(_FAKE_BULK_STATS_SRIOV))
        indexes = vmstats._find_bulk_stats_reverse_map(
            bulk_stats[0], 'net')
        assert indexes

    def test_log_inexistent_key(self):
        KEY = 'this.key.cannot.exist'
        sample = {}
        vm = FakeVM()
        log = FakeLogger()
        with MonkeyPatchScope(
            [(vmstats, '_log', log)]
        ):
            with vmstats._skip_if_missing_stats(vm):
                sample[KEY]
        assert len(log.messages) == 1
        assert log.messages[0][0] == logging.WARNING
        assert KEY in log.messages[0][1]


@expandPermutations
class NetworkStatsTests(VmStatsTestCase):

    # TODO: grab them from the schema
    _EXPECTED_KEYS = (
        'macAddr',
        'name',
        'speed',
        'state',
        'rxErrors',
        'rxDropped',
        'txErrors',
        'txDropped',
        'rx',
        'tx',
        'sampleTime',
    )

    def test_nic_have_all_keys(self):
        nic = FakeNic(name='vnet0', model='virtio',
                      mac_addr='00:1a:4a:16:01:51',
                      is_hostdevice=False)
        testvm = FakeVM(nics=(nic,))

        stats = vmstats._nic_traffic(
            testvm, nic,
            self.bulk_stats, 0,
            self.bulk_stats, 0,
        )

        self.assertStatsHaveKeys(stats, self._EXPECTED_KEYS)

    def test_networks_have_all_keys(self):
        nics = (
            FakeNic(name='vnet0', model='virtio',
                    mac_addr='00:1a:4a:16:01:51',
                    is_hostdevice=False),
        )
        vm = FakeVM(nics=nics)

        stats = {}
        vmstats.networks(vm, stats,
                         self.bulk_stats, self.bulk_stats,
                         self.interval)
        self.assertRepeatedStatsHaveKeys(nics, stats['network'],
                                         self._EXPECTED_KEYS)

    def test_networks_good_interval(self):
        nics = (
            FakeNic(name='vnet0', model='virtio',
                    mac_addr='00:1a:4a:16:01:51',
                    is_hostdevice=False),
        )
        vm = FakeVM(nics=nics)

        stats = {}
        assert vmstats.networks(
            vm, stats, self.bulk_stats, self.bulk_stats, 1
        )

    @permutations([[-42], [0]])
    def test_networks_bad_interval(self, interval):
        nics = (
            FakeNic(name='vnet0', model='virtio',
                    mac_addr='00:1a:4a:16:01:51',
                    is_hostdevice=False),
        )
        vm = FakeVM(nics=nics)

        stats = {}
        assert vmstats.networks(
            vm, stats, self.bulk_stats, self.bulk_stats, 0
        ) is None

    @permutations([
        ['net.0.rx.bytes'], ['net.0.rx.pkts'],
        ['net.0.rx.errs'], ['net.0.rx.drop'], ['net.0.tx.bytes'],
        ['net.0.tx.pkts'], ['net.0.tx.errs'], ['net.0.tx.drop'],
    ])
    def test_networks_missing_key(self, key):
        nics = (
            FakeNic(name='vnet0', model='virtio',
                    mac_addr='00:1a:4a:16:01:51',
                    is_hostdevice=False),
        )
        vm = FakeVM(nics=nics)
        vm.migrationPending = True

        faulty_bulk_stats = {}
        faulty_bulk_stats.update(self.bulk_stats)
        del faulty_bulk_stats[key]

        stats = {}
        assert vmstats.networks(
            vm, stats, self.bulk_stats, faulty_bulk_stats, 1
        )


class DiskStatsTests(VmStatsTestCase):

    # TODO: grab them from the schema
    # Note: these are the minimal set Vdsm exported,
    # no clear rationale for this subset.
    _EXPECTED_KEYS = (
        'truesize',
        'apparentsize',
        'readLatency',
        'writeLatency',
        'flushLatency',
        'imageID',
        # TODO: add test for 'lunGUID'
        'readRate',
        'writeRate',
        'readOps',
        'writeOps',
        'readBytes',
        'writtenBytes',
    )

    def test_disk_all_keys_present(self):
        interval = 10  # seconds
        drives = (FakeDrive(name='hdc', size=700 * MiB),)
        testvm = FakeVM(drives=drives)

        stats = {}
        stats_before = copy.deepcopy(self.bulk_stats)
        stats_after = copy.deepcopy(self.bulk_stats)
        _ensure_delta(stats_before, stats_after, 'block.0.rd.reqs', KiB)
        _ensure_delta(stats_before, stats_after, 'block.0.rd.bytes', 128 * KiB)
        vmstats.disks(testvm, stats,
                      stats_before, stats_after,
                      interval)
        self.assertRepeatedStatsHaveKeys(drives, stats['disks'],
                                         self._EXPECTED_KEYS)

    def test_interval_zero(self):
        interval = 0  # seconds
        # with zero interval, we won't have {read,write}Rate
        expected_keys = tuple(k for k in self._EXPECTED_KEYS
                              if k not in ('readRate', 'writeRate'))
        drives = (FakeDrive(name='hdc', size=700 * MiB),)
        testvm = FakeVM(drives=drives)

        stats = {}
        self.assertNotRaises(vmstats.disks,
                             testvm, stats,
                             self.bulk_stats, self.bulk_stats,
                             interval)
        self.assertRepeatedStatsHaveKeys(drives,
                                         stats['disks'],
                                         expected_keys)

    def test_disk_missing_rate(self):
        partial_stats = self._drop_stats(
            ('block.0.rd.bytes', 'block.1.rd.bytes',
             'block.0.wr.bytes', 'block.1.wr.bytes'))

        interval = 10  # seconds
        drives = (FakeDrive(name='hdc', size=700 * MiB),)
        testvm = FakeVM(drives=drives)

        stats = {}
        self.assertNotRaises(vmstats.disks,
                             testvm, stats,
                             partial_stats, partial_stats,
                             interval)

    def test_disk_missing_latency(self):
        partial_stats = self._drop_stats(
            ('block.0.rd.times', 'block.1.rd.times',
             'block.0.wr.reqs', 'block.1.wr.reqs'))

        interval = 10  # seconds
        drives = (FakeDrive(name='hdc', size=700 * MiB),)
        testvm = FakeVM(drives=drives)

        stats = {}
        self.assertNotRaises(vmstats.disks,
                             testvm, stats,
                             partial_stats, partial_stats,
                             interval)

    def test_iotune(self):
        iotune = {
            'total_bytes_sec': 0,
            'read_bytes_sec': 1000,
            'write_bytes_sec': 1000,
            'total_iops_sec': 0,
            'write_iops_sec': 0,
            'read_iops_sec': 0
        }
        drive = FakeDrive(name='sda', size=8 * GiB)
        drive.path = '/fake/path'
        drive.iotune = iotune
        testvm = FakeVM(drives=(drive,))
        stats = {}
        self.assertNotRaises(vmstats.tune_io, testvm, stats)
        assert stats

    def _drop_stats(self, keys):
        partial_stats = copy.deepcopy(self.bulk_stats)
        for key in keys:
            del partial_stats[key]
        return partial_stats


FIRST_CPU_SAMPLE = {'cpu.user': 4740000000, 'cpu.system': 6490000000}

LAST_CPU_SAMPLE = {'cpu.user': 4760000000, 'cpu.system': 6500000000}


@expandPermutations
class CpuStatsTests(VmStatsTestCase):
    # all data stolen from Vdsm and/or virsh -r domstats

    INTERVAL = 15.  # seconds.

    # [first, last]
    # intentionally use only one sample, the other empty
    @permutations([[{}, {}],
                   [{}, FIRST_CPU_SAMPLE],
                   [FIRST_CPU_SAMPLE, {}]])
    def test_empty_samples(self, first, last):
        stats = {}
        res = vmstats.cpu(stats, {}, {}, self.INTERVAL)
        assert stats == \
            {'cpuUsage': 0.0, 'cpuUser': 0.0, 'cpuSys': 0.0}
        assert res is None

    def test_only_cpu_user_system(self):
        stats = {}
        res = vmstats.cpu(stats, FIRST_CPU_SAMPLE, LAST_CPU_SAMPLE,
                          self.INTERVAL)
        assert stats == {
            'cpuUser': 0.0,
            'cpuSys': 0.2,
            'cpuUsage': '11260000000',
        }
        assert res is None

    def test_update_all_keys(self):
        stats = {}
        first_sample = {'cpu.time': 24345584838}
        first_sample.update(FIRST_CPU_SAMPLE)
        last_sample = {'cpu.time': 24478198023}
        last_sample.update(LAST_CPU_SAMPLE)
        res = vmstats.cpu(stats, first_sample, last_sample,
                          self.INTERVAL)
        assert stats == {
            'cpuUser': 0.6840879,
            'cpuSys': 0.2,
            'cpuUsage': '11260000000',
        }
        assert res is not None

    @permutations([
        # interval
        (-1,),
        (0,),
    ])
    def test_bad_interval(self, interval):
        stats = {}
        res = vmstats.cpu(stats, FIRST_CPU_SAMPLE, LAST_CPU_SAMPLE, interval)
        assert res is None

    @permutations([
        # sample, expected
        (None, {}),
        ({}, {}),
        ({'vcpu.current': -1}, {}),
        ({'vcpu.current': 4}, {'vcpuCount': 4}),
    ])
    def test_cpu_count(self, sample, expected):
        stats = {}
        self.assertNotRaises(vmstats.cpu_count, stats, sample)
        assert stats == expected


class BalloonStatsTests(VmStatsTestCase):

    def test_missing_data(self):
        stats = {}
        vm = FakeVM()
        self.assertNotRaises(
            vmstats.balloon,
            vm, stats, {}
        )
        assert 'balloonInfo' in stats
        info = stats['balloonInfo']
        self.assertStatsHaveKeys(
            info,
            ('balloon_max', 'balloon_min',
             'balloon_cur', 'balloon_target')
        )

    def test_balloon_current_default_zero(self):
        stats = {}
        vm = FakeVM()
        vmstats.balloon(vm, stats, {})
        assert stats['balloonInfo']['balloon_cur'] == '0'

    def test_log_missing_key(self):
        stats = {}
        vm = FakeVM()
        log = FakeLogger()
        with MonkeyPatchScope(
            [(vmstats, '_log', log)]
        ):
            vmstats.balloon(vm, stats, {})
        assert len(log.messages) == 1
        assert log.messages[0][0] == logging.WARNING
        assert 'balloon.current' in log.messages[0][1]


# helpers

def _ensure_delta(stats_before, stats_after, key, delta):
    """
    Set stats_before[key] and stats_after[key] so that
    stats_after[key] - stats_before[key] == abs(delta).
    """
    stats_before[key] = 0
    stats_after[key] = abs(delta)


class FakeNic(object):

    def __init__(self, name, model, mac_addr, is_hostdevice):
        self.name = name
        self.nicModel = model
        self.macAddr = mac_addr
        self.is_hostdevice = is_hostdevice


class FakeDrive(object):

    def __init__(self, name, size):
        self.name = name
        self.apparentsize = size
        self.truesize = size
        self.GUID = str(uuid.uuid4())
        self.imageID = str(uuid.uuid4())
        self.domainID = str(uuid.uuid4())
        self.poolID = str(uuid.uuid4())
        self.volumeID = str(uuid.uuid4())

    def __contains__(self, item):
        # isVdsmImage support
        return item in ('imageID', 'domainID', 'poolID', 'volumeID')


class FakeVM(object):

    def __init__(self, nics=None, drives=None):
        self.id = str(uuid.uuid4())
        self.nics = nics if nics is not None else []
        self.drives = drives if drives is not None else []
        self.migrationPending = False

    @property
    def monitorable(self):
        return not self.migrationPending

    def getNicDevices(self):
        return self.nics

    def getDiskDevices(self):
        return self.drives

    def mem_size_mb(self):
        # no specific meaning, just need to be realistic (e.g. not _1_)
        return 256

    def get_balloon_info(self):
        # no specific meaning, just need to be realistic (e.g. not _1_)
        # unit is KiB
        return {
            'target': 256 * KiB,
            'minimum': 256 * KiB,
        }
