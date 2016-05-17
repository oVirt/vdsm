#
# Copyright 2014 Red Hat, Inc.
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

import Queue
import logging
import threading
import time

from contextlib import contextmanager

from vdsm.storage import exception as se

from monkeypatch import MonkeyPatchScope
from storagefakelib import FakeStorageDomainCache
from testlib import VdsmTestCase
from testlib import expandPermutations, permutations
from testlib import make_config
from testlib import maybefail

from storage import monitor

MONITOR_INTERVAL = 0.2
CYCLE_TIMEOUT = 5.0

log = logging.getLogger("test")


class FakeEvent(object):
    """
    Fake vdsm.storage.misc.Event, keeping emiting events into a list. The
    original class is starting a new thread for each event, making it hard to
    test.
    """

    def __init__(self):
        self.received = []

    def emit(self, *args, **kwargs):
        log.debug("Emitting event (args=%s, kwrags=%s)", args, kwargs)
        self.received.append((args, kwargs))


class FakeDomain(object):
    """
    Fake storage domain implementing the minimal interface required for domain
    monitoring.
    """

    def __init__(self, sdUUID, version=1, iso_dir=None):
        self.sdUUID = sdUUID
        self.version = version
        self.iso_dir = iso_dir
        self.acquired = False
        self.stats = {
            'disktotal': '100',
            'diskfree': '50',
            'mdavalid': True,
            'mdathreshold': True,
            'mdasize': 0,
            'mdafree': 0,
        }
        # Test may set errors here to make method calls raise expected or
        # unexpected errors.
        self.errors = {}

    @maybefail
    def selftest(self):
        log.debug("Performing selftest")

    @maybefail
    def getReadDelay(self):
        log.debug("Checking read delay")
        return 0.005

    @maybefail
    def getStats(self):
        log.debug("Getting stats")
        return {'disktotal': '100',
                'diskfree': '50',
                'mdavalid': True,
                'mdathreshold': True,
                'mdasize': 0,
                'mdafree': 0}

    @maybefail
    def validateMaster(self):
        log.debug("Validating master")
        return {'valid': True, 'mount': True}

    def hasHostId(self, hostId):
        log.debug("Checking if host id is acquired")
        return self.acquired

    @maybefail
    def acquireHostId(self, hostId, async=True):
        log.debug("Acquiring host id (hostId=%s, async=%s)", hostId, async)
        assert not self.acquired, "Attempt to acquire acquired host id"
        self.acquired = True

    def releaseHostId(self, hostId, unused=True):
        log.debug("Releasing host id (hostId=%s, unused=%s)", hostId, unused)
        assert self.acquired, "Attempt to release unacquired host id"
        self.acquired = False

    @maybefail
    def getVersion(self):
        log.debug("Getting version")
        return self.version

    @maybefail
    def isISO(self):
        log.debug("Checking if iso domain")
        return self.iso_dir is not None

    def getIsoDomainImagesDir(self):
        return self.iso_dir


class UnexpectedError(Exception):
    pass


class MonitorEnv(object):

    def __init__(self, thread, event):
        self.thread = thread
        self.event = event
        self.queue = Queue.Queue()
        self.thread.cycleCallback = self._callback

    def wait_for_cycle(self):
        try:
            self.queue.get(True, CYCLE_TIMEOUT)
        except Queue.Empty:
            raise RuntimeError("Timeout waiting for monitor cycle")

    def _callback(self):
        self.queue.put(None)


@contextmanager
def monitor_env(shutdown=False, refresh=300):
    config = make_config([
        ("irs", "repo_stats_cache_refresh_timeout", str(refresh))
    ])
    with MonkeyPatchScope([
        (monitor, "sdCache", FakeStorageDomainCache()),
        (monitor, 'config', config),
    ]):
        event = FakeEvent()
        thread = monitor.MonitorThread('uuid', 'host_id', MONITOR_INTERVAL,
                                       event)
        try:
            yield MonitorEnv(thread, event)
        finally:
            thread.stop(shutdown=shutdown)
            try:
                thread.join()
            except RuntimeError as e:
                log.error("Error joining thread: %s", e)


class TestMonitorThreadIdle(VdsmTestCase):

    def test_initial_status(self):
        thread = monitor.MonitorThread('uuid', 'host_id', 0.2, None)
        status = thread.getStatus()
        self.assertFalse(status.actual)
        self.assertTrue(status.valid)


@expandPermutations
class TestMonitorThreadSetup(VdsmTestCase):

    # in this state we do:
    # 1. If refresh timeout has expired, remove the domain from the cache
    # 2. If domain was not produced yet, produce the domain
    # 3. If the domain is an iso domain, initialze isoPrefix
    #
    # - On failure we abort the process, and set status.error.
    # - On the first failure, we emit a domain state change event with
    #   valid=False.
    # - We retry these operations forever until both succeed, or monitor is
    #   stopped.

    def test_produce_retry(self):
        with monitor_env() as env:
            env.thread.start()

            # First cycle will fail since domain does not exist
            env.wait_for_cycle()
            status = env.thread.getStatus()
            self.assertTrue(status.actual)
            self.assertFalse(status.valid)
            self.assertIsInstance(status.error, se.StorageDomainDoesNotExist)
            self.assertEqual(env.event.received, [(('uuid', False), {})])
            del env.event.received[0]

            # Seocnd cycle will fail but no event should be emitted
            env.wait_for_cycle()
            status = env.thread.getStatus()
            self.assertFalse(status.valid)
            self.assertIsInstance(status.error, se.StorageDomainDoesNotExist)
            self.assertEqual(env.event.received, [])

            # Third cycle should succeed
            monitor.sdCache.domains["uuid"] = FakeDomain("uuid")
            env.wait_for_cycle()
            status = env.thread.getStatus()
            self.assertTrue(status.valid)
            self.assertEqual(env.event.received, [(('uuid', True), {})])

    @permutations([[OSError], [UnexpectedError]])
    def test_iso_domain_retry(self, exception):
        with monitor_env() as env:
            # Add inaccessible iso domain
            domain = FakeDomain("uuid", iso_dir="/path")
            domain.errors["isISO"] = exception
            monitor.sdCache.domains["uuid"] = domain
            env.thread.start()

            # First cycle will fail in domain.isISO
            env.wait_for_cycle()
            status = env.thread.getStatus()
            self.assertTrue(status.actual)
            self.assertIsNone(status.isoPrefix)
            self.assertFalse(status.valid)
            self.assertIsInstance(status.error, exception)
            self.assertEqual(env.event.received, [(('uuid', False), {})])
            del env.event.received[0]

            # Seocnd cycle will fail but no event should be emitted
            env.wait_for_cycle()
            status = env.thread.getStatus()
            self.assertFalse(status.valid)
            self.assertIsInstance(status.error, exception)
            self.assertEqual(env.event.received, [])

            # Third cycle should succeed
            del domain.errors["isISO"]
            env.wait_for_cycle()
            status = env.thread.getStatus()
            self.assertEqual(status.isoPrefix, domain.iso_dir)
            self.assertTrue(status.valid)
            self.assertEqual(env.event.received, [(('uuid', True), {})])

    def test_refresh(self):
        with monitor_env(refresh=MONITOR_INTERVAL * 1.5) as env:
            # Add inaccessible iso domain to keep in setup state
            domain = FakeDomain("uuid", iso_dir="/path")
            domain.errors["isISO"] = OSError
            monitor.sdCache.domains["uuid"] = domain
            env.thread.start()

            # Domain will be removed after the refresh timeout
            env.wait_for_cycle()
            self.assertIn(domain.sdUUID, monitor.sdCache.domains)
            env.wait_for_cycle()
            # Refresh timeout will expires during next cycle
            env.wait_for_cycle()
            self.assertNotIn(domain.sdUUID, monitor.sdCache.domains)


@expandPermutations
class TestMonitorThreadMonitoring(VdsmTestCase):

    # In this state we do:
    # 1. If refresh timeout has expired, remove the domain from the cache
    # 2. call domain.selftest()
    # 3. call domain.getReadDelay()
    # 4. call domain.getStats()
    # 5. call domain.validateMaster()
    # 6. call domain.hasHostId()
    # 7. call domain.getVersion()
    #
    # - On failure, we abort the process, and set status.error.
    # - If this is the first failure, we emit a domain state change event with
    #   valid=False.
    # - On success, if the previous status.error was set, we emit a domain
    #   state change event with valid=True.
    # - If everything was successful, and host id not acquired yet, acquire it
    # - We repeat this forever until the monitor is stopped.

    def test_unknown_to_valid(self):
        with monitor_env() as env:
            monitor.sdCache.domains["uuid"] = FakeDomain("uuid")
            env.thread.start()
            env.wait_for_cycle()
            status = env.thread.getStatus()
            self.assertTrue(status.actual)
            self.assertTrue(status.valid)
            self.assertEqual(env.event.received, [(('uuid', True), {})])

    @permutations([
        ("getReadDelay", se.MiscFileReadException),
        ("getReadDelay", UnexpectedError),
        ("selftest", OSError),
        ("selftest", UnexpectedError),
        ("getStats", se.FileStorageDomainStaleNFSHandle),
        ("getStats", se.StorageDomainAccessError),
        ("getStats", UnexpectedError),
        ("validateMaster", OSError),
        ("validateMaster", UnexpectedError),
        ("getVersion", OSError),
        ("getVersion", UnexpectedError),
    ])
    def test_from_unknown_to_invalid(self, method, exception):
        with monitor_env() as env:
            domain = FakeDomain("uuid")
            domain.errors[method] = exception
            monitor.sdCache.domains["uuid"] = domain
            env.thread.start()
            env.wait_for_cycle()
            status = env.thread.getStatus()
            self.assertFalse(status.valid)
            self.assertIsInstance(status.error, exception)
            self.assertEqual(env.event.received, [(('uuid', False), {})])

    @permutations([
        ("getReadDelay", se.MiscFileReadException),
        ("getReadDelay", UnexpectedError),
        ("selftest", OSError),
        ("selftest", UnexpectedError),
        ("getStats", se.FileStorageDomainStaleNFSHandle),
        ("getStats", se.StorageDomainAccessError),
        ("getStats", UnexpectedError),
        ("validateMaster", OSError),
        ("validateMaster", UnexpectedError),
        ("getVersion", OSError),
        ("getVersion", UnexpectedError),
    ])
    def test_from_invalid_to_valid(self, method, exception):
        with monitor_env() as env:
            domain = FakeDomain("uuid")
            domain.errors[method] = exception
            monitor.sdCache.domains["uuid"] = domain
            env.thread.start()
            env.wait_for_cycle()
            del env.event.received[0]
            del domain.errors[method]
            env.wait_for_cycle()
            status = env.thread.getStatus()
            self.assertTrue(status.valid)
            self.assertEqual(env.event.received, [(('uuid', True), {})])

    def test_keeps_valid(self):
        with monitor_env() as env:
            monitor.sdCache.domains["uuid"] = FakeDomain("uuid")
            env.thread.start()
            env.wait_for_cycle()
            del env.event.received[0]
            env.wait_for_cycle()
            status = env.thread.getStatus()
            self.assertTrue(status.valid)
            # No event emited on the second cycle, domain monitor state did not
            # change (valid -> valid)
            self.assertEqual(env.event.received, [])

    @permutations([
        ("getReadDelay", se.MiscFileReadException),
        ("getReadDelay", UnexpectedError),
        ("selftest", OSError),
        ("selftest", UnexpectedError),
        ("getStats", se.FileStorageDomainStaleNFSHandle),
        ("getStats", se.StorageDomainAccessError),
        ("getStats", UnexpectedError),
        ("validateMaster", OSError),
        ("validateMaster", UnexpectedError),
        ("getVersion", OSError),
        ("getVersion", UnexpectedError),
    ])
    def test_from_valid_to_invalid(self, method, exception):
        with monitor_env() as env:
            domain = FakeDomain("uuid")
            monitor.sdCache.domains["uuid"] = domain
            env.thread.start()
            env.wait_for_cycle()
            del env.event.received[0]
            domain.errors[method] = exception
            env.wait_for_cycle()
            status = env.thread.getStatus()
            self.assertFalse(status.valid)
            self.assertIsInstance(status.error, exception)
            self.assertEqual(env.event.received, [(('uuid', False), {})])

    def test_acquire_host_id(self):
        with monitor_env() as env:
            domain = FakeDomain("uuid")
            monitor.sdCache.domains["uuid"] = domain
            env.thread.start()
            env.wait_for_cycle()
            self.assertTrue(domain.acquired)

    def test_acquire_host_id_after_error(self):
        with monitor_env() as env:
            domain = FakeDomain("uuid")
            domain.errors['getReadDelay'] = se.MiscFileReadException
            monitor.sdCache.domains["uuid"] = domain
            env.thread.start()
            env.wait_for_cycle()
            del domain.errors["getReadDelay"]
            env.wait_for_cycle()
            self.assertTrue(domain.acquired)

    def test_acquire_host_id_if_lost(self):
        with monitor_env() as env:
            domain = FakeDomain("uuid")
            monitor.sdCache.domains["uuid"] = domain
            env.thread.start()
            env.wait_for_cycle()
            # Simulate loosing host id
            domain.acquired = False
            env.wait_for_cycle()
            self.assertTrue(domain.acquired)

    def test_dont_acquire_host_id_on_iso_domain(self):
        with monitor_env() as env:
            domain = FakeDomain("uuid", iso_dir="/path")
            monitor.sdCache.domains["uuid"] = domain
            env.thread.start()
            env.wait_for_cycle()
            self.assertFalse(domain.acquired)

    def test_dont_acquire_host_id_on_error(self):
        with monitor_env() as env:
            domain = FakeDomain("uuid")
            domain.errors['getReadDelay'] = se.MiscFileReadException
            monitor.sdCache.domains["uuid"] = domain
            env.thread.start()
            env.wait_for_cycle()
            self.assertFalse(domain.acquired)

    @permutations([[se.AcquireHostIdFailure], [UnexpectedError]])
    def test_acquire_host_id_retry_after_error(self, exception):
        with monitor_env() as env:
            domain = FakeDomain("uuid")
            domain.errors['acquireHostId'] = exception
            monitor.sdCache.domains["uuid"] = domain
            env.thread.start()
            env.wait_for_cycle()
            self.assertFalse(domain.acquired)
            del domain.errors["acquireHostId"]
            env.wait_for_cycle()
            self.assertTrue(domain.acquired)

    def test_refresh(self):
        with monitor_env(refresh=MONITOR_INTERVAL * 1.5) as env:
            domain = FakeDomain("uuid")
            monitor.sdCache.domains["uuid"] = domain
            env.thread.start()

            # Domain will be removed after the refresh timeout
            env.wait_for_cycle()
            self.assertIn(domain.sdUUID, monitor.sdCache.domains)
            env.wait_for_cycle()
            # Refresh timeout will expires during next cycle
            env.wait_for_cycle()
            self.assertNotIn(domain.sdUUID, monitor.sdCache.domains)


class TestMonitorThreadStopping(VdsmTestCase):

    # Here we release the host id if we acquired it, and the monitor was
    # stopped with shutdown=False.

    def test_stop(self):
        with monitor_env(shutdown=False) as env:
            domain = FakeDomain("uuid")
            monitor.sdCache.domains["uuid"] = domain
            env.thread.start()
            env.wait_for_cycle()
        self.assertFalse(domain.acquired)

    def test_shutdown(self):
        with monitor_env(shutdown=True) as env:
            domain = FakeDomain("uuid")
            monitor.sdCache.domains["uuid"] = domain
            env.thread.start()
            env.wait_for_cycle()
        self.assertTrue(domain.acquired)

    def test_stop_while_blocked(self):
        with monitor_env(shutdown=False) as env:
            domain = FakeDomain("uuid")
            blocked = threading.Event()

            def block():
                blocked.set()
                time.sleep(MONITOR_INTERVAL)

            domain.getReadDelay = block
            monitor.sdCache.domains["uuid"] = domain
            env.thread.start()
            if not blocked.wait(CYCLE_TIMEOUT):
                raise RuntimeError("Timeout waiting for calling getReadDelay")

        status = env.thread.getStatus()
        self.assertFalse(status.actual)
        self.assertFalse(domain.acquired)


class FrozenStatusTests(VdsmTestCase):

    def setUp(self):
        self.status = monitor.Status()
        self.frozen = monitor.FrozenStatus(self.status)

    def test_copy_attributes(self):
        for name in self.status.__slots__:
            value = getattr(self.status, name)
            expected = getattr(self.frozen, name)
            self.assertEquals(value, expected)

    def test_setting_attribute_raises(self):
        for name in self.status.__slots__:
            self.assertRaises(AssertionError, setattr, self.frozen, name, 1)

    def test_deleting_attribute_raises(self):
        for name in self.status.__slots__:
            self.assertRaises(AssertionError, delattr, self.frozen, name)

    def test_valid(self):
        self.assertEqual(self.frozen.valid, self.status.valid)


class StatusValidTests(VdsmTestCase):

    def test_valid(self):
        s = monitor.Status()
        self.assertIsNone(s.error)
        self.assertTrue(s.valid)

    def test_invalid(self):
        s = monitor.Status()
        s.error = Exception()
        self.assertFalse(s.valid)
