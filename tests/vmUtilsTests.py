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

from virt import utils
from virt import vm
from virt import vmexitreason

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
        self.assertEquals(self.cache.get('the answer'), None)

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
