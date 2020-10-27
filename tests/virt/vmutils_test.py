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

from six.moves import range

import os

from vdsm.virt import vmexitreason
from vdsm.virt import utils
from vdsm.virt import vm

from monkeypatch import MonkeyPatchScope
from testlib import permutations, expandPermutations
from testlib import VdsmTestCase as TestCaseBase
import pytest


class ExpiringCacheOperationTests(TestCaseBase):
    def setUp(self):
        self.cache = utils.ExpiringCache(ttl=20)

    def test_setitem_getitem_same_key(self):
        self.cache['the answer'] = 42
        assert 42 == self.cache['the answer']

    def test_setitem_get_same_key(self):
        self.cache['the answer'] = 42
        assert 42 == self.cache.get('the answer')

    def test_setitem_get_same_key_with_default(self):
        self.cache['the answer'] = 42
        assert 42 == self.cache.get('the answer', 'default')

    def test_setitem_get_different_key_with_default(self):
        value = self.cache.get('a different answer', 'default')
        assert value == 'default'

    def test_get_key_without_explicit_default(self):
        assert self.cache.get('a key noone added') is None

    def test_getitem_missing_key(self):
        with pytest.raises(KeyError):
            self.cache['FIZZBUZZ']

    def test_delitem_existing_key(self):
        self.cache['the answer'] = 42
        del self.cache['the answer']
        assert self.cache.get('the answer') is None

    def test_delitem_missing_key(self):
        def _del(key):
            del self.cache[key]
        with pytest.raises(KeyError):
            _del('this key does not exist')

    def test_clear(self):
        ITEMS = 10
        for i in range(ITEMS):
            self.cache[i] = 'foobar-%d' % i

        self.cache.clear()

        for i in range(ITEMS):
            self.cache.get(i) is None

    def test_nonzero(self):
        assert not self.cache
        self.cache['foo'] = 'bar'
        assert self.cache


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
        assert 42 == cache['the answer']
        clock.now = 1.0
        assert cache.get('the answer') is None
        clock.now = 1.000001
        assert cache.get('the answer') is None

    def test_nonzero_full_expiration(self):
        clock = FakeClock(0.0)
        cache = utils.ExpiringCache(ttl=1.0, clock=clock)

        ITEMS = 10
        for i in range(ITEMS):
            cache[i] = 'foobar-%d' % i
        assert cache

        clock.now = 1.1
        assert not cache

    def test_nonzero_partial_expiration(self):
        clock = FakeClock(0.0)
        cache = utils.ExpiringCache(ttl=2.0, clock=clock)

        cache['a'] = 1
        clock.now = 1.0
        assert cache

        cache['b'] = 2
        clock.now = 2.0
        assert cache

        clock.now = 3.0
        assert not cache


class ExceptionsTests(TestCaseBase):

    def test_MissingLibvirtDomainError(self):
        try:
            raise vm.MissingLibvirtDomainError()
        except vm.MissingLibvirtDomainError as e:
            assert e.reason == \
                vmexitreason.LIBVIRT_DOMAIN_MISSING
            assert str(e) == \
                vmexitreason.exitReasons.get(
                    vmexitreason.LIBVIRT_DOMAIN_MISSING)


@expandPermutations
class LibvirtEventDispatchTests(TestCaseBase):

    @permutations([[-1], [1023]])
    def test_eventToString_unknown_event(self, code):
        assert vm.eventToString(code)


class DynamicSemaphoreTests(TestCaseBase):

    INITIAL_BOUND = 5
    INCREASED_BOUND = 10

    def setUp(self):
        self.sem = utils.DynamicBoundedSemaphore(self.INITIAL_BOUND)

    def assertAcquirable(self, times=1):
        for i in range(times):
            success = self.sem.acquire(blocking=False)
            assert success, 'It should be possible to obtain Dynamic Semaphore'

    def assertNotAcquirable(self):
        success = self.sem.acquire(blocking=False)
        assert not success, ('It should not be possible to obtain '
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


class TestTimedAcquireLock(TestCaseBase):

    def setUp(self):
        self.lockid = 'test'
        self.lock = utils.TimedAcquireLock(self.lockid)

    def test_acquire_free_not_raises(self):
        flow = 'external'
        assert self.lock.holder is None
        self.assertNotRaises(self.lock.acquire, 0.0, flow)
        assert self.lock.holder == flow
        self.lock.release()
        assert self.lock.holder is None

    def test_acquire_raises_timeout(self):
        self.lock.acquire(0.0, flow='external')
        try:
            with pytest.raises(utils.LockTimeout):
                self.lock.acquire(0.0, 'internal')
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

        assert exc is not None
        assert exc.lockid == self.lockid
        assert exc.flow == 'external'


class TestRunLogging(object):

    def test_success(self, tmp_path):
        with MonkeyPatchScope([(utils, '_COMMANDS_LOG_DIR', str(tmp_path))]):
            log_path = utils.run_logging(['/bin/true'])
            assert os.path.isabs(log_path)
            assert os.path.isfile(log_path)

    def test_log_content(self, tmp_path):
        with MonkeyPatchScope([(utils, '_COMMANDS_LOG_DIR', str(tmp_path))]):
            log_path = utils.run_logging(
                ["sh", "-c", "echo out >&1; echo err >&2"])
            assert os.path.isabs(log_path)
            assert os.path.isfile(log_path)
            with open(log_path, 'rb') as f:
                log_content = f.read()
            assert log_content == b'out\nerr\n'

    def test_error(self, tmp_path):
        with MonkeyPatchScope([(utils, '_COMMANDS_LOG_DIR', str(tmp_path))]):
            with pytest.raises(utils.LoggingError) as e:
                utils.run_logging(['/bin/false'])
            assert e.value.rc == 1
            assert os.path.isabs(e.value.log_path)
            assert os.path.isfile(e.value.log_path)

    def test_bad_command(self, tmp_path):
        with MonkeyPatchScope([(utils, '_COMMANDS_LOG_DIR', str(tmp_path))]):
            with pytest.raises(utils.LoggingError) as e:
                utils.run_logging(['/foobarbaz'])
            assert os.path.isabs(e.value.log_path)
            assert os.path.isfile(e.value.log_path)
