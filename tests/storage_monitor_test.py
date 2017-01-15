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

import logging
import threading
import time

from contextlib import contextmanager

from six.moves import queue

from vdsm.storage import exception as se

from monkeypatch import MonkeyPatch
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
    Fake vdsm.storage.misc.Event, keeping emitting events into a list. The
    original class is starting a new thread for each event, making it hard to
    test.
    """

    def __init__(self):
        self.received = []

    def emit(self, *args, **kwargs):
        log.debug("Emitting event (args=%s, kwrags=%s)", args, kwargs)
        self.received.append((args, kwargs))


class FakeCheckService(object):
    """
    Fake vdsm.storage.check.CheckService, keeping registered callbacks but not
    doing anything.

    The test code should use the registered callback to submit check results.
    """

    def __init__(self):
        self.checkers = {}

    def start_checking(self, path, complete, interval=10.0):
        log.info("Start checking %r", path)
        if path in self.checkers:
            raise RuntimeError("Already checking path %r" % path)
        self.checkers[path] = (complete, interval)

    def stop_checking(self, path, timeout=None):
        log.info("Stop checking %r", path)
        self.checkers.pop(path)

    def complete(self, path, result):
        callback = self.checkers[path][0]
        callback(result)


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

    def getMonitoringPath(self):
        return "/path/to/metadata"

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
        # Releasing unacquired host is not an error, but this should not fail
        # in the tests.
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


class FakeCheckResult(object):

    def __init__(self, error=None):
        self.error = error

    def delay(self):
        if self.error:
            raise self.error
        return 0.005


class MonitorEnv(object):

    def __init__(self, thread, event, checker):
        self.thread = thread
        self.event = event
        self.checker = checker
        self.queue = queue.Queue()
        self.thread.cycleCallback = self._callback

    def wait_for_cycle(self):
        try:
            self.queue.get(True, CYCLE_TIMEOUT)
        except queue.Empty:
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
        checker = FakeCheckService()
        thread = monitor.MonitorThread('uuid', 'host_id', MONITOR_INTERVAL,
                                       event, checker)
        try:
            yield MonitorEnv(thread, event, checker)
        finally:
            thread.stop(shutdown=shutdown)
            try:
                thread.join()
            except RuntimeError as e:
                log.error("Error joining thread: %s", e)


class TestMonitorThreadIdle(VdsmTestCase):

    def test_initial_status(self):
        thread = monitor.MonitorThread('uuid', 'host_id', 0.2, None, None)
        status = thread.getStatus()
        self.assertFalse(status.actual)
        self.assertTrue(status.valid)


@expandPermutations
class TestMonitorThreadSetup(VdsmTestCase):

    # in this state we do:
    # 1. If refresh timeout has expired, remove the domain from the cache
    # 2. If domain was not produced yet, produce the domain
    # 3. If domain monitoring path is available, start checking path using the
    #    domain checker.
    # 4. If the domain is an iso domain, initialize isoPrefix
    #
    # - On failure we abort the process, and set status.error.
    # - On the first failure, we emit a domain state change event with
    #   valid=False.
    # - We retry these operations forever until all succeed, or the monitor is
    #   stopped.

    def test_start_checking_path(self):
        with monitor_env() as env:
            domain = FakeDomain("uuid")
            monitor.sdCache.domains["uuid"] = domain
            env.thread.start()
            env.wait_for_cycle()
            _, interval = env.checker.checkers[domain.getMonitoringPath()]
            self.assertEqual(interval, MONITOR_INTERVAL)

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

            # Second cycle will fail but no event should be emitted
            env.wait_for_cycle()
            status = env.thread.getStatus()
            self.assertFalse(status.valid)
            self.assertIsInstance(status.error, se.StorageDomainDoesNotExist)
            self.assertEqual(env.event.received, [])

            # Third cycle should succeed but no event should be emitted since
            # we don't have path status yet.
            domain = FakeDomain("uuid")
            monitor.sdCache.domains["uuid"] = domain
            env.wait_for_cycle()
            status = env.thread.getStatus()
            self.assertTrue(status.valid)
            self.assertEqual(env.event.received, [])

            # When path status is available, emit event
            env.checker.complete(domain.getMonitoringPath(), FakeCheckResult())
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

            # Second cycle will fail but no event should be emitted
            env.wait_for_cycle()
            status = env.thread.getStatus()
            self.assertFalse(status.valid)
            self.assertIsInstance(status.error, exception)
            self.assertEqual(env.event.received, [])

            # Third cycle should succeed but no event should be emitted since
            # we don't have path status yet.
            del domain.errors["isISO"]
            env.wait_for_cycle()
            status = env.thread.getStatus()
            self.assertEqual(status.isoPrefix, domain.iso_dir)
            self.assertTrue(status.valid)
            self.assertEqual(env.event.received, [])

            # When path status is available, emit event
            env.checker.complete(domain.getMonitoringPath(), FakeCheckResult())
            status = env.thread.getStatus()
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
    # 3. call domain.getStats()
    # 4. call domain.validateMaster()
    # 5. call domain.hasHostId()
    # 6. call domain.getVersion()
    #
    # - When path check completes, we get a callback from the checker thread
    #   and update monitor status.
    # - On failure, we abort the process, and set status.error.
    # - If this is the first failure, we emit a domain state change event with
    #   valid=False.
    # - On success, if the previous status.error was set, we emit a domain
    #   state change event with valid=True.
    # - If everything was successful, and host id not acquired yet, acquire it
    # - We repeat this forever until the monitor is stopped.

    def test_unknown_to_valid(self):
        with monitor_env() as env:
            domain = FakeDomain("uuid")
            monitor.sdCache.domains["uuid"] = domain
            env.thread.start()

            # First cycle suceeds, but path status is not avialale yet
            env.wait_for_cycle()
            status = env.thread.getStatus()
            self.assertFalse(status.actual)
            self.assertEqual(env.event.received, [])

            # When path succeeds, emit VALID event
            env.checker.complete(domain.getMonitoringPath(), FakeCheckResult())
            status = env.thread.getStatus()
            self.assertTrue(status.actual)
            self.assertTrue(status.valid)
            self.assertEqual(env.event.received, [(('uuid', True), {})])

    @permutations([
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
    def test_from_unknown_to_invalid_domain(self, method, exception):
        with monitor_env() as env:
            domain = FakeDomain("uuid")
            domain.errors[method] = exception
            monitor.sdCache.domains["uuid"] = domain
            env.thread.start()

            # First cycle fail, emit event without waiting for path status
            env.wait_for_cycle()
            status = env.thread.getStatus()
            self.assertTrue(status.actual)
            self.assertFalse(status.valid)
            self.assertIsInstance(status.error, exception)
            self.assertEqual(env.event.received, [(('uuid', False), {})])

    @permutations([[se.MiscFileReadException], [UnexpectedError]])
    def test_from_unknown_to_invalid_path(self, exception):
        with monitor_env() as env:
            domain = FakeDomain("uuid")
            monitor.sdCache.domains["uuid"] = domain
            env.thread.start()

            # First cycle succeed, but path status is not available yet
            env.wait_for_cycle()
            status = env.thread.getStatus()
            self.assertFalse(status.actual)
            self.assertEqual(env.event.received, [])

            # When path fail, emit INVALID event
            env.checker.complete(domain.getMonitoringPath(),
                                 FakeCheckResult(exception))
            status = env.thread.getStatus()
            self.assertTrue(status.actual)
            self.assertFalse(status.valid)
            self.assertIsInstance(status.error, exception)
            self.assertEqual(env.event.received, [(('uuid', False), {})])

    @permutations([
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
    def test_from_invalid_to_valid_domain(self, method, exception):
        with monitor_env() as env:
            domain = FakeDomain("uuid")
            domain.errors[method] = exception
            monitor.sdCache.domains["uuid"] = domain
            env.thread.start()

            # First cycle fail, and emit INVALID event
            env.wait_for_cycle()
            del env.event.received[0]

            # Path status succeeds, but domain status is not valid, so no event
            # is emitted.
            env.wait_for_cycle()
            env.checker.complete(domain.getMonitoringPath(), FakeCheckResult())
            status = env.thread.getStatus()
            self.assertTrue(status.actual)
            self.assertFalse(status.valid)
            self.assertEqual(env.event.received, [])

            # When next cycle succeeds, emit VALID event
            del domain.errors[method]
            env.wait_for_cycle()
            status = env.thread.getStatus()
            self.assertTrue(status.valid)
            self.assertEqual(env.event.received, [(('uuid', True), {})])

    @permutations([[se.MiscFileReadException], [UnexpectedError]])
    def test_from_invalid_to_valid_path(self, exception):
        with monitor_env() as env:
            domain = FakeDomain("uuid")
            monitor.sdCache.domains["uuid"] = domain
            env.thread.start()

            # First cycle succeed, but path status fail, emit INVALID event
            env.wait_for_cycle()
            env.checker.complete(domain.getMonitoringPath(),
                                 FakeCheckResult(exception))
            del env.event.received[0]

            # Both domain status and pass status succeed, emit VALID event
            env.wait_for_cycle()
            env.checker.complete(domain.getMonitoringPath(), FakeCheckResult())
            status = env.thread.getStatus()
            self.assertTrue(status.valid)
            self.assertEqual(env.event.received, [(('uuid', True), {})])

    def test_keeps_valid(self):
        with monitor_env() as env:
            domain = FakeDomain("uuid")
            monitor.sdCache.domains["uuid"] = domain
            env.thread.start()

            # Both domain status and path status succeed and emit VALID event
            env.wait_for_cycle()
            env.checker.complete(domain.getMonitoringPath(), FakeCheckResult())
            del env.event.received[0]

            # Both succeed again, no event emitted - domain monitor state did
            # not change (valid -> valid)
            env.wait_for_cycle()
            env.checker.complete(domain.getMonitoringPath(), FakeCheckResult())
            status = env.thread.getStatus()
            self.assertTrue(status.valid)
            self.assertEqual(env.event.received, [])

    @permutations([
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
    def test_from_valid_to_invalid_domain(self, method, exception):
        with monitor_env() as env:
            domain = FakeDomain("uuid")
            monitor.sdCache.domains["uuid"] = domain
            env.thread.start()

            # Both domain status and path status succeed and emit VALID event
            env.wait_for_cycle()
            env.checker.complete(domain.getMonitoringPath(), FakeCheckResult())
            del env.event.received[0]

            # Domain status fail, emit INVALID event
            domain.errors[method] = exception
            env.wait_for_cycle()
            status = env.thread.getStatus()
            self.assertFalse(status.valid)
            self.assertIsInstance(status.error, exception)
            self.assertEqual(env.event.received, [(('uuid', False), {})])

    @permutations([[se.MiscFileReadException], [UnexpectedError]])
    def test_from_valid_to_invalid_path(self, exception):
        with monitor_env() as env:
            domain = FakeDomain("uuid")
            monitor.sdCache.domains["uuid"] = domain
            env.thread.start()

            # Both domain status and path status succeed and emit VALID event
            env.wait_for_cycle()
            env.checker.complete(domain.getMonitoringPath(), FakeCheckResult())
            del env.event.received[0]

            env.wait_for_cycle()
            env.checker.complete(domain.getMonitoringPath(),
                                 FakeCheckResult(exception))
            status = env.thread.getStatus()
            self.assertFalse(status.valid)
            self.assertIsInstance(status.error, exception)
            self.assertEqual(env.event.received, [(('uuid', False), {})])

    def test_acquire_host_id(self):
        with monitor_env() as env:
            domain = FakeDomain("uuid")
            monitor.sdCache.domains["uuid"] = domain
            env.thread.start()

            # Both domain status and path status succeed
            env.wait_for_cycle()
            env.checker.complete(domain.getMonitoringPath(), FakeCheckResult())
            self.assertFalse(domain.acquired)

            # Acquire host id on the next cycle
            env.wait_for_cycle()
            self.assertTrue(domain.acquired)

    def test_acquire_host_id_after_error(self):
        with monitor_env() as env:
            domain = FakeDomain("uuid")
            domain.errors["selftest"] = OSError
            monitor.sdCache.domains["uuid"] = domain
            env.thread.start()

            # Domain status fail, emit INVALID event
            env.wait_for_cycle()
            del domain.errors["selftest"]

            # Both domain status and path status succeed
            env.wait_for_cycle()
            env.checker.complete(domain.getMonitoringPath(), FakeCheckResult())
            self.assertFalse(domain.acquired)

            # Acquire host id on the next cycle
            env.wait_for_cycle()
            self.assertTrue(domain.acquired)

    def test_acquire_host_id_if_lost(self):
        with monitor_env() as env:
            domain = FakeDomain("uuid")
            monitor.sdCache.domains["uuid"] = domain
            env.thread.start()

            # Both domain status and path status succeed
            env.wait_for_cycle()
            env.checker.complete(domain.getMonitoringPath(), FakeCheckResult())
            self.assertFalse(domain.acquired)

            # Acquire host id on the next cycle
            env.wait_for_cycle()
            self.assertTrue(domain.acquired)

            # Simulate loosing host id - acquire again because status is valid
            domain.acquired = False
            env.wait_for_cycle()
            self.assertTrue(domain.acquired)

    def test_dont_acquire_host_id_on_iso_domain(self):
        with monitor_env() as env:
            domain = FakeDomain("uuid", iso_dir="/path")
            monitor.sdCache.domains["uuid"] = domain
            env.thread.start()
            env.wait_for_cycle()
            env.checker.complete(domain.getMonitoringPath(), FakeCheckResult())
            self.assertFalse(domain.acquired)

    def test_dont_acquire_host_id_on_error(self):
        with monitor_env() as env:
            domain = FakeDomain("uuid")
            domain.errors["selftest"] = OSError
            monitor.sdCache.domains["uuid"] = domain
            env.thread.start()
            env.wait_for_cycle()
            env.checker.complete(domain.getMonitoringPath(), FakeCheckResult())
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
            env.checker.complete(domain.getMonitoringPath(), FakeCheckResult())
            self.assertFalse(domain.acquired)

            # Acquire on next cycle
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
            env.checker.complete(domain.getMonitoringPath(), FakeCheckResult())
        self.assertFalse(domain.acquired)

    def test_shutdown(self):
        with monitor_env(shutdown=True) as env:
            domain = FakeDomain("uuid")
            monitor.sdCache.domains["uuid"] = domain
            env.thread.start()
            env.wait_for_cycle()
            env.checker.complete(domain.getMonitoringPath(), FakeCheckResult())
            # Acquire on next cycle
            env.wait_for_cycle()
        self.assertTrue(domain.acquired)

    def test_stop_while_blocked(self):
        with monitor_env(shutdown=False) as env:
            domain = FakeDomain("uuid")
            blocked = threading.Event()

            def block():
                blocked.set()
                time.sleep(MONITOR_INTERVAL)

            domain.selftest = block
            monitor.sdCache.domains["uuid"] = domain
            env.thread.start()
            if not blocked.wait(CYCLE_TIMEOUT):
                raise RuntimeError("Timeout waiting for calling getReadDelay")

        status = env.thread.getStatus()
        self.assertFalse(status.actual)
        self.assertFalse(domain.acquired)

    def test_stop_checking_path(self):
        with monitor_env() as env:
            domain = FakeDomain("uuid")
            monitor.sdCache.domains["uuid"] = domain
            env.thread.start()
            env.wait_for_cycle()
        self.assertFalse(domain.acquired)
        self.assertNotIn(domain.getMonitoringPath(), env.checker.checkers)


@expandPermutations
class TestStatus(VdsmTestCase):

    def test_initial_status(self):
        # For backward compatibility, we must publish an initial status before
        # we collect the first samples. The initial status is marked as
        # actual=False to allow engine to treat it specially.
        path_status = monitor.PathStatus(actual=False)
        domain_status = monitor.DomainStatus(actual=False)
        status = monitor.Status(path_status, domain_status)
        self.assertFalse(status.actual)
        self.assertIsNone(status.error)
        self.assertTrue(status.valid)

    def test_partial_status(self):
        # We collected path status but domain status is not available yet.
        path_status = monitor.PathStatus()
        domain_status = monitor.DomainStatus(actual=False)
        status = monitor.Status(path_status, domain_status)
        self.assertFalse(status.actual)
        self.assertIsNone(status.error)
        self.assertTrue(status.valid)

    def test_full_status(self):
        # Both path status and domain status are available.
        path_status = monitor.PathStatus()
        domain_status = monitor.DomainStatus()
        status = monitor.Status(path_status, domain_status)
        self.assertTrue(status.actual)
        self.assertIsNone(status.error)
        self.assertTrue(status.valid)

    def test_path_error(self):
        path_status = monitor.PathStatus(error=Exception("path"))
        domain_status = monitor.DomainStatus()
        status = monitor.Status(path_status, domain_status)
        self.assertTrue(status.actual)
        self.assertEqual(status.error, path_status.error)
        self.assertFalse(status.valid)

    def test_path_error_non_actual_domain_status(self):
        path_status = monitor.PathStatus(error=Exception("path"))
        domain_status = monitor.DomainStatus(actual=False)
        status = monitor.Status(path_status, domain_status)
        self.assertTrue(status.actual)
        self.assertEqual(status.error, path_status.error)
        self.assertFalse(status.valid)

    def test_domain_error(self):
        path_status = monitor.PathStatus()
        domain_status = monitor.DomainStatus(error=Exception("domain"))
        status = monitor.Status(path_status, domain_status)
        self.assertTrue(status.actual)
        self.assertEqual(status.error, domain_status.error)
        self.assertFalse(status.valid)

    def test_domain_error_non_actual_path_status(self):
        path_status = monitor.PathStatus(actual=False)
        domain_status = monitor.DomainStatus(error=Exception("domain"))
        status = monitor.Status(path_status, domain_status)
        self.assertTrue(status.actual)
        self.assertEqual(status.error, domain_status.error)
        self.assertFalse(status.valid)

    def test_both_error(self):
        # For backward compatibility we have to present single error.
        path_status = monitor.PathStatus(error=Exception("path"))
        domain_status = monitor.DomainStatus(error=Exception("domain"))
        status = monitor.Status(path_status, domain_status)
        self.assertTrue(status.actual)
        self.assertEqual(status.error, path_status.error)
        self.assertFalse(status.valid)

    @permutations([
        ("valid", True),
        ("error", None),
        ("actual", True),
        ("checkTime", 1234567),
        ("readDelay", 0),
        ("diskUtilization", (None, None)),
        ("masterMounted", False),
        ("masterValid", False),
        ("hasHostId", False),
        ("vgMdUtilization", (0, 0)),
        ("vgMdHasEnoughFreeSpace", True),
        ("vgMdFreeBelowThreashold", True),
        ("isoPrefix", None),
        ("version", -1),
    ])
    @MonkeyPatch(time, 'time', lambda: 1234567)
    def test_readonly_attributes(self, attr, value):
        status = monitor.Status(monitor.PathStatus(), monitor.DomainStatus())
        self.assertEqual(value, getattr(status, attr))
        self.assertRaises(AttributeError, setattr, status, attr, "new")
