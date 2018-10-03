#
# Copyright 2015-2017 Red Hat, Inc.
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

from six.moves import range

from vdsm.virt import vmexitreason
from vdsm.virt import utils
from vdsm.virt import vm

from testlib import permutations, expandPermutations
from testlib import VdsmTestCase as TestCaseBase


class ExpiringCacheOperationTests(TestCaseBase):
    def setUp(self):
        self.cache = utils.ExpiringCache(ttl=20)

    def test_setitem_getitem_same_key(self):
        self.cache['the answer'] = 42
        self.assertEqual(42, self.cache['the answer'])

    def test_setitem_get_same_key(self):
        self.cache['the answer'] = 42
        self.assertEqual(42, self.cache.get('the answer'))

    def test_setitem_get_same_key_with_default(self):
        self.cache['the answer'] = 42
        self.assertEqual(42, self.cache.get('the answer', 'default'))

    def test_setitem_get_different_key_with_default(self):
        value = self.cache.get('a different answer', 'default')
        self.assertEqual(value, 'default')

    def test_get_key_without_explicit_default(self):
        self.assertEqual(None, self.cache.get('a key noone added'))

    def test_getitem_missing_key(self):
        self.assertRaises(KeyError,
                          lambda key: self.cache[key],
                          'FIZZBUZZ')

    def test_delitem_existing_key(self):
        self.cache['the answer'] = 42
        del self.cache['the answer']
        self.assertEqual(self.cache.get('the answer'), None)

    def test_delitem_missing_key(self):
        def _del(key):
            del self.cache[key]
        self.assertRaises(KeyError,
                          _del,
                          'this key does not exist')

    def test_clear(self):
        ITEMS = 10
        for i in range(ITEMS):
            self.cache[i] = 'foobar-%d' % i

        self.cache.clear()

        for i in range(ITEMS):
            self.cache.get(i) is None

    def test_nonzero(self):
        self.assertFalse(self.cache)
        self.cache['foo'] = 'bar'
        self.assertTrue(self.cache)


class FakeClock(object):
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


class ExpirationTests(TestCaseBase):
    def test_key_expiration(self):
        clock = FakeClock(0.0)
        cache = utils.ExpiringCache(ttl=1.0, clock=clock)
        cache['the answer'] = 42
        clock.now = 0.999999
        self.assertEqual(42, cache['the answer'])
        clock.now = 1.0
        self.assertEqual(None, cache.get('the answer'))
        clock.now = 1.000001
        self.assertEqual(None, cache.get('the answer'))

    def test_nonzero_full_expiration(self):
        clock = FakeClock(0.0)
        cache = utils.ExpiringCache(ttl=1.0, clock=clock)

        ITEMS = 10
        for i in range(ITEMS):
            cache[i] = 'foobar-%d' % i
        self.assertTrue(cache)

        clock.now = 1.1
        self.assertFalse(cache)

    def test_nonzero_partial_expiration(self):
        clock = FakeClock(0.0)
        cache = utils.ExpiringCache(ttl=2.0, clock=clock)

        cache['a'] = 1
        clock.now = 1.0
        self.assertTrue(cache)

        cache['b'] = 2
        clock.now = 2.0
        self.assertTrue(cache)

        clock.now = 3.0
        self.assertFalse(cache)


class ExceptionsTests(TestCaseBase):

    def test_MissingLibvirtDomainError(self):
        try:
            raise vm.MissingLibvirtDomainError()
        except vm.MissingLibvirtDomainError as e:
            self.assertEqual(
                e.reason,
                vmexitreason.LIBVIRT_DOMAIN_MISSING)
            self.assertEqual(
                str(e),
                vmexitreason.exitReasons.get(
                    vmexitreason.LIBVIRT_DOMAIN_MISSING))


@expandPermutations
class LibvirtEventDispatchTests(TestCaseBase):

    @permutations([[-1], [1023]])
    def test_eventToString_unknown_event(self, code):
        self.assertTrue(vm.eventToString(code))


class DynamicSemaphoreTests(TestCaseBase):

    INITIAL_BOUND = 5
    INCREASED_BOUND = 10

    def setUp(self):
        self.sem = utils.DynamicBoundedSemaphore(self.INITIAL_BOUND)

    def assertAcquirable(self, times=1):
        for i in range(times):
            success = self.sem.acquire(blocking=False)
            self.assertTrue(success, 'It should be possible to obtain '
                                     'Dynamic Semaphore')

    def assertNotAcquirable(self):
        success = self.sem.acquire(blocking=False)
        self.assertFalse(success, 'It should not be possible to obtain '
                                  'Dynamic Semaphore with value 0')

    def test_basic_operations(self):
        self.assertAcquirable(times=self.INITIAL_BOUND)
        self.sem.release()
        self.assertAcquirable()

    def test_bound_increase(self):
        self.sem.bound = self.INCREASED_BOUND
        self.assertAcquirable(times=self.INCREASED_BOUND)
        self.assertNotAcquirable()

    def test_bound_decrease(self):
        self.sem.bound = 0
        self.assertNotAcquirable()

    def test_bound_increase_while_acquired(self):
        self.assertAcquirable(times=self.INITIAL_BOUND)
        self.sem.bound = self.INCREASED_BOUND
        added_capacity = self.INCREASED_BOUND - self.INITIAL_BOUND
        self.assertAcquirable(times=added_capacity)
        self.assertNotAcquirable()

    def test_bound_decrease_while_acquired(self):
        self.assertAcquirable(times=3)
        self.sem.bound = 4
        self.assertAcquirable()
        self.assertNotAcquirable()

    def test_bound_decrease_below_capacity_while_acquired(self):
        self.assertAcquirable(times=3)
        self.sem.bound = 1
        self.assertNotAcquirable()


@expandPermutations
class TestIsKvm(TestCaseBase):

    def test_empty(self):
        # ensure backward compatibility
        self.assertTrue(utils.is_kvm({}))

    @permutations([
        # container_type
        ['rkt'],
        ['foobar'],  # we don't validate the value
    ])
    def test_detects_container_type(self, container_type):
        self.assertFalse(utils.is_kvm({
            'containerType': container_type,
        }))


XML_TEMPLATE = u'''<domain type='kvm' id='1'>
  <name>a0_41</name>
  <uuid>13070562-2ee7-4092-a746-7975ff5b3993</uuid>
  <metadata
        xmlns:ovirt-tune="http://ovirt.org/vm/tune/1.0"
        xmlns:ovirt-vm="http://ovirt.org/vm/1.0">
    <ovirt-tune:qos/>
    <ovirt-vm:vm xmlns:ovirt-vm="http://ovirt.org/vm/1.0">
      <ovirt-vm:destroy_on_reboot type="bool">False
        </ovirt-vm:destroy_on_reboot>
      <ovirt-vm:guestAgentAPIVersion type="int">0
        </ovirt-vm:guestAgentAPIVersion>
      <ovirt-vm:memGuaranteedSize type="int">4096
        </ovirt-vm:memGuaranteedSize>
      <ovirt-vm:startTime type="float">1518107638.94
        </ovirt-vm:startTime>
      {cluster_version}
    </ovirt-vm:vm>
  </metadata>
</domain>'''


@expandPermutations
class TestHasXmlConfiguration(TestCaseBase):

    @permutations([
        # tag, expected_result
        ['xml', True],
        ['_srcDomXML', False],
    ])
    def test_no_metadata(self, tag, expected_result):
        test_xml = u'''<domain type='kvm' id='1'>
          <name>a0_41</name>
          <uuid>13070562-2ee7-4092-a746-7975ff5b3993</uuid>
        </domain>'''
        params = {tag: test_xml}
        self.assertEqual(
            utils.has_xml_configuration(params),
            expected_result)

    def test_detects_creation(self):
        test_xml = XML_TEMPLATE.format(cluster_version='')
        params = {'xml': test_xml}
        self.assertTrue(utils.has_xml_configuration(params))

    @permutations([
        # cluster_version, expected_result
        ('', False),
        ('<clusterVersion>4.2</clusterVersion>', True),
        ('<ovirt-vm:clusterVersion>4.2</ovirt-vm:clusterVersion>', True),
        ('<ovirt-vm:clusterVersion>4.3</ovirt-vm:clusterVersion>', True),
    ])
    def test_detects_migration(self, cluster_version, expected_result):
        test_xml = XML_TEMPLATE.format(cluster_version=cluster_version)
        params = {'_srcDomXML': test_xml}
        self.assertEqual(
            utils.has_xml_configuration(params),
            expected_result)


class TestTimedAcquireLock(TestCaseBase):

    def setUp(self):
        self.lockid = 'test'
        self.lock = utils.TimedAcquireLock(self.lockid)

    def test_acquire_free_not_raises(self):
        flow = 'external'
        self.assertIs(self.lock.holder, None)
        self.assertNotRaises(self.lock.acquire, 0.0, flow)
        self.assertEqual(self.lock.holder, flow)
        self.lock.release()
        self.assertIs(self.lock.holder, None)

    def test_acquire_raises_timeout(self):
        self.lock.acquire(0.0, flow='external')
        try:
            self.assertRaises(
                utils.LockTimeout,
                self.lock.acquire,
                0.0, 'internal')
        finally:
            self.lock.release()

    def test_exception_context(self):
        exc = None
        self.lock.acquire(0.0, flow='external')
        try:
            self.lock.acquire(0.0, flow='internal')
        except utils.LockTimeout as x:
            exc = x
        finally:
            self.lock.release()

        self.assertIsNot(exc, None)
        self.assertEqual(exc.lockid, self.lockid)
        self.assertEqual(exc.flow, 'external')
