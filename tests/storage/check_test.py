#
# Copyright 2016-2017 Red Hat, Inc.
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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#

from __future__ import print_function

import logging
import os
import pprint
import re
import threading
import time
from contextlib import contextmanager

import pytest

from fakelib import FakeLogger
from monkeypatch import MonkeyPatch
from monkeypatch import MonkeyPatchScope
from testlib import VdsmTestCase
from testlib import expandPermutations, permutations
from testlib import start_thread
from testlib import temporaryPath

from vdsm import constants
from vdsm.common import concurrent
from vdsm.storage import check
from vdsm.storage import asyncevent
from vdsm.storage import exception


@expandPermutations
class TestDirectioChecker(VdsmTestCase):

    def setUp(self):
        self.loop = asyncevent.EventLoop()
        self.results = []
        self.checks = 1

    def tearDown(self):
        self.loop.close()

    def complete(self, result):
        self.results.append(result)
        if len(self.results) == self.checks:
            self.loop.stop()

    def test_path_missing(self):
        self.checks = 1
        checker = check.DirectioChecker(self.loop, "/no/such/path",
                                        self.complete)
        checker.start()
        self.loop.run_forever()
        pprint.pprint(self.results)
        result = self.results[0]
        self.assertRaises(exception.MiscFileReadException, result.delay)

    def test_path_ok(self):
        self.checks = 1
        with temporaryPath(data=b"blah") as path:
            checker = check.DirectioChecker(self.loop, path, self.complete)
            checker.start()
            self.loop.run_forever()
            pprint.pprint(self.results)
            result = self.results[0]
            delay = result.delay()
            print("delay:", delay)
            self.assertEqual(type(delay), float)

    @MonkeyPatch(constants, "EXT_DD", "/no/such/executable")
    def test_executable_missing(self):
        self.checks = 1
        with temporaryPath(data=b"blah") as path:
            checker = check.DirectioChecker(self.loop, path, self.complete)
            checker.start()
            self.loop.run_forever()
            pprint.pprint(self.results)
            result = self.results[0]
            self.assertRaises(exception.MiscFileReadException, result.delay)

    @MonkeyPatch(constants, "EXT_TASKSET", "/no/such/executable")
    def test_taskset_missing(self):
        self.checks = 1
        with temporaryPath(data=b"blah") as path:
            checker = check.DirectioChecker(self.loop, path, self.complete)
            checker.start()
            self.loop.run_forever()
            pprint.pprint(self.results)
            result = self.results[0]
            self.assertRaises(exception.MiscFileReadException, result.delay)

    @pytest.mark.slow
    @permutations([
        # interval, delay, expected
        (0.20, 0.10, 0.20),
        (0.10, 0.12, 0.20),
    ])
    def test_interval(self, interval, delay, expected):
        self.checks = 5
        clock_res = 0.01
        with fake_dd(delay):
            checker = check.DirectioChecker(self.loop, "/path", self.complete,
                                            interval=interval)
            checker.start()
            self.loop.run_forever()
            pprint.pprint(self.results)
            for i in range(self.checks - 1):
                r1 = self.results[i]
                r2 = self.results[i + 1]
                actual = r2.time - r1.time
                self.assertAlmostEqual(actual, expected, delta=clock_res)

    @MonkeyPatch(check, "_log", FakeLogger(logging.WARNING))
    def test_block_warnings(self):
        self.checks = 1
        with fake_dd(0.3):
            checker = check.DirectioChecker(self.loop, "/path", self.complete,
                                            interval=0.2)
            checker.start()
            self.loop.run_forever()
        msg = check._log.messages[0][1]
        # Matching time value is too fragile
        r = re.compile(r"Checker '/path' is blocked for .+ seconds")
        self.assertRegexpMatches(msg, r)

    # In the idle state the checker is not running so there is nothing to
    # cleanup.

    def test_idle_stop_ignored(self):
        checker = check.DirectioChecker(self.loop, "/path", self.complete)
        checker.stop()  # Will be ignored
        self.assertFalse(checker.is_running())

    def test_idle_repr(self):
        checker = check.DirectioChecker(self.loop, "/path", self.complete)
        print(checker)
        self.assertIn("/path", str(checker))
        self.assertIn(check.IDLE, str(checker))
        self.assertNotIn("next_check=", str(checker))

    # In the running state, the checker complete callback will stop the event
    # loop. We need to run the loop until it is stopped.

    def test_running_start_raises(self):
        checker = check.DirectioChecker(self.loop, "/path", self.complete)
        checker.start()
        try:
            self.assertRaises(RuntimeError, checker.start)
        finally:
            self.loop.run_forever()

    def test_running_repr(self):
        checker = check.DirectioChecker(self.loop, "/path", self.complete)
        checker.start()
        try:
            print(checker)
            self.assertIn("/path", str(checker))
            self.assertIn(check.RUNNING, str(checker))
            self.assertIn("next_check=", str(checker))
        finally:
            self.loop.run_forever()

    # In the stopping state, the checker will not call the complete callback.
    # We need to wait on the checker and stop the loop when it completes.

    def test_stopping_stop_ignored(self):
        checker = check.DirectioChecker(self.loop, "/path", self.complete)
        checker.start()
        try:
            checker.stop()
            checker.stop()  # Will be ignored
            self.assertTrue(checker.is_running())
        finally:
            start_thread(self.wait_for_checker, checker)
            self.loop.run_forever()

    def test_stopping_start_raises(self):
        checker = check.DirectioChecker(self.loop, "/path", self.complete)
        checker.start()
        try:
            checker.stop()
            self.assertRaises(RuntimeError, checker.start)
        finally:
            start_thread(self.wait_for_checker, checker)
            self.loop.run_forever()

    def test_stopping_repr(self):
        checker = check.DirectioChecker(self.loop, "/path", self.complete)
        checker.start()
        try:
            checker.stop()
            print(checker)
            self.assertIn("/path", str(checker))
            self.assertIn(check.STOPPING, str(checker))
            self.assertNotIn("next_check=", str(checker))
        finally:
            start_thread(self.wait_for_checker, checker)
            self.loop.run_forever()

    def wait_for_checker(self, checker):
        checker.wait(5)
        self.loop.call_soon_threadsafe(self.loop.stop)


@expandPermutations
class TestDirectioCheckerWaiting(VdsmTestCase):

    def setUp(self):
        self.loop = asyncevent.EventLoop()
        self.thread = concurrent.thread(self.loop.run_forever)
        self.thread.start()
        self.completed = threading.Event()

    def tearDown(self):
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join()
        self.loop.close()

    def complete(self, result):
        self.completed.set()

    def test_running_stop_during_wait(self):
        checker = check.DirectioChecker(self.loop, "/path", self.complete)
        self.loop.call_soon_threadsafe(checker.start)
        self.assertTrue(self.completed.wait(1.0))
        self.loop.call_soon_threadsafe(checker.stop)
        self.assertTrue(checker.wait(1.0))
        self.assertFalse(checker.is_running())

    @pytest.mark.slow
    def test_running_stop_during_check(self):
        with fake_dd(0.2):
            checker = check.DirectioChecker(self.loop, "/path", self.complete)
            self.loop.call_soon_threadsafe(checker.start)
            self.loop.call_soon_threadsafe(checker.stop)
            self.assertTrue(checker.wait(1.0))
            self.assertFalse(self.completed.is_set())
            self.assertFalse(checker.is_running())

    @pytest.mark.slow
    def test_stopping_timeout(self):
        with fake_dd(0.2):
            checker = check.DirectioChecker(self.loop, "/path", self.complete)
            self.loop.call_soon_threadsafe(checker.start)
            self.loop.call_soon_threadsafe(checker.stop)
            self.assertFalse(checker.wait(0.1))
            self.assertTrue(checker.is_running())


@expandPermutations
class TestDirectioCheckerTimings(VdsmTestCase):

    def setUp(self):
        self.loop = asyncevent.EventLoop()
        self.results = []

    def tearDown(self):
        self.loop.close()

    def complete(self, result):
        self.results.append(result)
        if len(self.results) == self.checkers:
            self.loop.stop()

    @pytest.mark.slow
    @permutations([[1], [50], [100], [200]])
    def test_path_ok(self, checkers):
        self.checkers = checkers
        with temporaryPath(data=b"blah") as path:
            start = time.time()
            for i in range(checkers):
                checker = check.DirectioChecker(self.loop, path, self.complete)
                checker.start()
            self.loop.run_forever()
            elapsed = time.time() - start
            self.assertEqual(len(self.results), self.checkers)
            print("%d checkers: %f seconds" % (checkers, elapsed))
            # Make sure all succeeded
            for res in self.results:
                res.delay()

    @pytest.mark.slow
    @permutations([[1], [50], [100], [200]])
    def test_path_missing(self, checkers):
        self.checkers = checkers
        start = time.time()
        for i in range(checkers):
            checker = check.DirectioChecker(self.loop, "/no/such/path",
                                            self.complete)
            checker.start()
        self.loop.run_forever()
        elapsed = time.time() - start
        self.assertEqual(len(self.results), self.checkers)
        print("%d checkers: %f seconds" % (checkers, elapsed))
        # Make sure all failed
        for res in self.results:
            self.assertRaises(exception.MiscFileReadException, res.delay)


@expandPermutations
class TestCheckResult(VdsmTestCase):

    @permutations([
        # err, seconds
        (b"1\n2\n1 byte (1 B) copied, 1 s, 1 B/s\n",
            1.0),
        (b"1\n2\n1024 bytes (1 kB) copied, 1 s, 1 kB/s\n",
            1.0),
        (b"1\n2\n1572864 bytes (1.5 MB) copied, 1.5 s, 1 MB/s\n",
            1.5),
        (b"1\n2\n1610612736 bytes (1.5 GB) copied, 1000.5 s, 1.53 MB/s\n",
            1000.5),
        (b"1\n2\n479 bytes (479 B) copied, 5.6832e-05 s, 8.4 MB/s\n",
            5.6832e-05),
        (b"1\n2\n512 bytes (512e-3 MB) copied, 1 s, 512e-3 MB/s\n",
            1.0),
        (b"1\n2\n524288 bytes (512e3 B) copied, 1 s, 512e3 B/s\n",
            1.0),
        (b"1\n2\n517 bytes (517 B) copied, 0 s, Infinity B/s\n",
            0.0),
        (b"1\n2\n4096 bytes (4.1 kB, 4.0 KiB) copied, "
         b"0.00887814 s, 461 kB/s\n",
            0.00887814),
        (b"1\n2\n30 bytes copied, 0.00156704 s, 19.1 kB/s",
            0.00156704),
    ])
    def test_success(self, err, seconds):
        result = check.CheckResult("/path", 0, err, 0, 0)
        self.assertEqual(result.delay(), seconds)

    def test_non_zero_exit_code(self):
        path = "/path"
        reason = "REASON"
        result = check.CheckResult(path, 1, reason, 0, 0)
        with self.assertRaises(exception.MiscFileReadException) as ctx:
            result.delay()
        self.assertIn(path, str(ctx.exception))
        self.assertIn(reason, str(ctx.exception))

    @permutations([
        (b"",),
        (b"1\n2\n\n",),
        (b"1\n2\n1024 bytes (1 kB) copied, BAD, 1 kB/s\n",),
        (b"1\n2\n1024 bytes (1 kB) copied, BAD s, 1 kB/s\n",),
        (b"1\n2\n1024 bytes (1 kB) copied, -1- s, 1 kB/s\n",),
        (b"1\n2\n1024 bytes (1 kB) copied, e3- s, 1 kB/s\n",),
    ])
    def test_unexpected_output(self, err):
        result = check.CheckResult("/path", 0, err, 0, 0)
        self.assertRaises(exception.MiscFileReadException, result.delay)


class TestCheckService(VdsmTestCase):

    def setUp(self):
        self.service = check.CheckService()
        self.service.start()
        self.result = None
        self.completed = threading.Event()

    def tearDown(self):
        self.service.stop()

    def complete(self, result):
        self.result = result
        self.completed.set()

    def test_start_checking(self):
        with fake_dd(0.0):
            self.service.start_checking("/path", self.complete)
            self.assertTrue(self.service.is_checking("/path"))
            self.assertTrue(self.completed.wait(1.0))
            self.assertEqual(self.result.rc, 0)

    def test_start_checking_already_watched(self):
        with fake_dd(0.0):
            self.service.start_checking("/path", self.complete)
            with self.assertRaises(RuntimeError):
                self.service.start_checking("/path", self.complete)

    def test_stop_checking(self):
        with fake_dd(0.0):
            self.service.start_checking("/path", self.complete)
            self.service.stop_checking("/path")
            self.assertFalse(self.service.is_checking("/path"))

    def test_stop_checking_not_watched(self):
        with self.assertRaises(KeyError):
            self.service.stop_checking("/path")

    def test_stop_checking_and_wait(self):
        with fake_dd(0.0):
            self.service.start_checking("/path", self.complete)
            self.assertTrue(self.service.stop_checking("/path", timeout=1.0))
            self.assertFalse(self.service.is_checking("/path"))

    @pytest.mark.slow
    def test_stop_checking_timeout(self):
        with fake_dd(0.2):
            self.service.start_checking("/path", self.complete)
            self.assertFalse(self.service.stop_checking("/path", timeout=0.1))
            self.assertFalse(self.service.is_checking("/path"))


@contextmanager
def fake_dd(delay):
    script = "#!/bin/sh\nsleep %.1f\n" % delay
    script = script.encode('ascii')
    with temporaryPath(data=script) as fake_dd:
        os.chmod(fake_dd, 0o700)
        with MonkeyPatchScope([(constants, "EXT_DD", fake_dd)]):
            yield
