#
# Copyright 2015 Red Hat, Inc.
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

import uuid

from virt import vmstats
from testlib import VdsmTestCase as TestCaseBase
from testlib import permutations, expandPermutations

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


class VmStatsTestCase(TestCaseBase):

    def setUp(self):
        # just pick one sampling
        self.samples = _FAKE_BULK_STATS.values()[0]
        self.bulk_stats = self.samples[0]
        self.interval = 10  # seconds

    def assertNameIsAt(self, stats, group, idx, name):
        self.assertEqual(stats['%s.%d.name' % (group, idx)], name)

    def assertStatsHaveKeys(self, stats):
        for key in self._EXPECTED_KEYS:
            self.assertIn(key, stats)

    def assertRepeatedStatsHaveKeys(self, items, stats):
        for item in items:
            self.assertStatsHaveKeys(stats[item.name])


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
        self.assertNotIn(name, indexes)

    @permutations([['block', 'hdc'], ['net', 'vnet0']])
    def test_index_can_change(self, group, name):
        all_indexes = []

        for bulk_stats in self.samples:
            indexes = vmstats._find_bulk_stats_reverse_map(
                bulk_stats, group)

            self.assertNameIsAt(bulk_stats, group, indexes[name], name)
            all_indexes.append(indexes)

        # and indeed indexes must change
        self.assertEqual(len(all_indexes), len(self.samples))


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
        'rxRate',
        'txRate',
        'rx',
        'tx',
        'sampleTime',
    )

    def test_nic_have_all_keys(self):
        nic = FakeNic(name='vnet0', model='virtio',
                      mac_addr='00:1a:4a:16:01:51')
        testvm = FakeVM(nics=(nic,))

        stats = vmstats._nic_traffic(
            testvm,
            nic.name, nic.nicModel, nic.macAddr,
            self.bulk_stats, 0,
            self.bulk_stats, 0,
            self.interval)

        self.assertStatsHaveKeys(stats)

    def test_networks_have_all_keys(self):
        nics = (
            FakeNic(name='vnet0', model='virtio',
                    mac_addr='00:1a:4a:16:01:51'),
        )
        vm = FakeVM(nics=nics)

        stats = {}
        vmstats.networks(vm, stats,
                         self.bulk_stats, self.bulk_stats,
                         self.interval)
        self.assertRepeatedStatsHaveKeys(nics, stats['network'])

    def test_networks_good_interval(self):
        nics = (
            FakeNic(name='vnet0', model='virtio',
                    mac_addr='00:1a:4a:16:01:51'),
        )
        vm = FakeVM(nics=nics)

        stats = {}
        self.assertTrue(
            vmstats.networks(vm, stats,
                             self.bulk_stats, self.bulk_stats,
                             1)
        )

    @permutations([[-42], [0]])
    def test_networks_bad_interval(self, interval):
        nics = (
            FakeNic(name='vnet0', model='virtio',
                    mac_addr='00:1a:4a:16:01:51'),
        )
        vm = FakeVM(nics=nics)

        stats = {}
        self.assertTrue(
            vmstats.networks(vm, stats,
                             self.bulk_stats, self.bulk_stats,
                             0) is None
        )


class DiskStatsTests(VmStatsTestCase):

    # TODO: grab them from the schema
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
        drives = (FakeDrive(name='hdc', size=700 * 1024 * 1024),)
        testvm = FakeVM(drives=drives)

        stats = {}
        vmstats.disks(testvm, stats,
                      self.bulk_stats, self.bulk_stats,
                      interval)
        self.assertRepeatedStatsHaveKeys(drives, stats['disks'])


# helpers

class FakeNic(object):

    def __init__(self, name, model, mac_addr):
        self.name = name
        self.nicModel = model
        self.macAddr = mac_addr


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

    def getNicDevices(self):
        return self.nics

    def getDiskDevices(self):
        return self.drives
