# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import logging
import os
import pprint
import re
import threading
import time

import pytest

from fakelib import FakeLogger
from testlib import start_thread
from testlib import temporaryPath

from vdsm.common import concurrent
from vdsm.common import constants
from vdsm.storage import check
from vdsm.storage import asyncevent
from vdsm.storage import exception


class TestDirectioChecker:

    def setup_method(self, m):
        self.loop = asyncevent.EventLoop()
        self.results = []
        self.checks = 1

    def teardown_method(self, m):
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
        with pytest.raises(exception.MiscFileReadException):
            result.delay()

    def test_path_missing_leak(self):
        fds_before = set(os.listdir("/proc/self/fd"))
        self.checks = 10
        checker = check.DirectioChecker(
            self.loop, "/no/such/path", self.complete, interval=0.1)
        checker.start()
        self.loop.run_forever()
        pprint.pprint(self.results)
        fds_after = set(os.listdir("/proc/self/fd"))
        assert fds_before == fds_after

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
            assert type(delay) == float

    def test_path_ok_leak(self):
        fds_before = set(os.listdir("/proc/self/fd"))
        self.checks = 10
        with temporaryPath(data=b"blah") as path:
            checker = check.DirectioChecker(
                self.loop, path, self.complete, interval=0.1)
            checker.start()
            self.loop.run_forever()
            pprint.pprint(self.results)
        fds_after = set(os.listdir("/proc/self/fd"))
        assert fds_before == fds_after

    def test_executable_missing(self, monkeypatch):
        monkeypatch.setattr(constants, "EXT_DD", "/no/such/executable")
        self.checks = 1
        with temporaryPath(data=b"blah") as path:
            checker = check.DirectioChecker(self.loop, path, self.complete)
            checker.start()
            self.loop.run_forever()
            pprint.pprint(self.results)
            result = self.results[0]
            with pytest.raises(exception.MiscFileReadException):
                result.delay()

    def test_taskset_missing(self, monkeypatch):
        monkeypatch.setattr(constants, "EXT_TASKSET", "/no/such/executable")
        self.checks = 1
        with temporaryPath(data=b"blah") as path:
            checker = check.DirectioChecker(self.loop, path, self.complete)
            checker.start()
            self.loop.run_forever()
            pprint.pprint(self.results)
            result = self.results[0]
            with pytest.raises(exception.MiscFileReadException):
                result.delay()

    @pytest.mark.slow
    @pytest.mark.parametrize('interval, delay, expected', [
        (0.20, 0.10, 0.20),
        (0.10, 0.12, 0.20),
    ])
    def test_interval(self, fake_dd, interval, delay, expected):
        self.checks = 5
        clock_res = 0.01
        fake_dd.configure(delay=delay)
        checker = check.DirectioChecker(
            self.loop,
            "/path",
            self.complete,
            interval=interval)

        checker.start()
        self.loop.run_forever()
        pprint.pprint(self.results)
        for i in range(self.checks - 1):
            r1 = self.results[i]
            r2 = self.results[i + 1]
            actual = r2.time - r1.time
            assert actual == pytest.approx(expected, abs=clock_res)

    # Handling timeout

    def test_timeout(self, fake_dd):
        # Expected events:
        # +0.0 start checker
        # +0.3 fail with timeout
        # +0.4 dd commpletes, result ignored
        # +0.5 loop stopped

        def complete(result):
            self.results.append(result)
            self.loop.call_later(0.2, self.loop.stop)

        fake_dd.configure(delay=0.4)
        checker = check.DirectioChecker(
            self.loop,
            "/path",
            complete,
            interval=0.3)
        checker.start()
        self.loop.run_forever()

        assert len(self.results) == 1
        with pytest.raises(exception.MiscFileReadException) as e:
            self.results[0].delay()
        assert "Read timeout" in str(e.value)

    def test_block_warnings(self, monkeypatch, fake_dd):
        monkeypatch.setattr(check, "_log", FakeLogger(logging.WARNING))
        # Expected events:
        # +0.0 start checker
        # +0.2 fail with timeout
        # +0.4 log warning
        # +0.5 checker stopped
        # +0.6 dd completes, result ignored
        # +0.7 loop stopped

        def complete(result):
            self.results.append(result)
            self.loop.call_later(0.3, checker.stop)
            self.loop.call_later(0.4, self.loop.stop)

        fake_dd.configure(delay=0.6)
        checker = check.DirectioChecker(
            self.loop,
            "/path",
            complete,
            interval=0.2)
        checker.start()
        self.loop.run_forever()

        assert len(check._log.messages) == 1
        # Matching time value is too fragile
        r = re.compile(r"Checker '/path' is blocked for .+ seconds")
        msg = check._log.messages[0][1]
        assert re.match(r, msg)

    # In the idle state the checker is not running so there is nothing to
    # cleanup.

    def test_idle_stop_ignored(self):
        checker = check.DirectioChecker(self.loop, "/path", self.complete)
        checker.stop()  # Will be ignored
        assert not checker.is_running()

    def test_idle_repr(self):
        checker = check.DirectioChecker(self.loop, "/path", self.complete)
        print(checker)
        assert "/path" in str(checker)
        assert check.IDLE in str(checker)
        assert "next_check=" not in str(checker)

    # In the running state, the checker complete callback will stop the event
    # loop. We need to run the loop until it is stopped.

    def test_running_start_raises(self):
        checker = check.DirectioChecker(self.loop, "/path", self.complete)
        checker.start()
        try:
            with pytest.raises(RuntimeError):
                checker.start()
        finally:
            self.loop.run_forever()

    def test_running_repr(self):
        checker = check.DirectioChecker(self.loop, "/path", self.complete)
        checker.start()
        try:
            print(checker)
            assert "/path" in str(checker)
            assert check.RUNNING in str(checker)
            assert "next_check=" in str(checker)
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
            assert checker.is_running()
        finally:
            start_thread(self.wait_for_checker, checker)
            self.loop.run_forever()

    def test_stopping_start_raises(self):
        checker = check.DirectioChecker(self.loop, "/path", self.complete)
        checker.start()
        try:
            checker.stop()
            with pytest.raises(RuntimeError):
                checker.start()
        finally:
            start_thread(self.wait_for_checker, checker)
            self.loop.run_forever()

    def test_stopping_repr(self):
        checker = check.DirectioChecker(self.loop, "/path", self.complete)
        checker.start()
        try:
            checker.stop()
            print(checker)
            assert "/path" in str(checker)
            assert check.STOPPING in str(checker)
            assert "next_check=" not in str(checker)
        finally:
            start_thread(self.wait_for_checker, checker)
            self.loop.run_forever()

    def wait_for_checker(self, checker):
        checker.wait(5)
        self.loop.call_soon_threadsafe(self.loop.stop)


class TestDirectioCheckerWaiting:

    def setup_method(self, m):
        self.loop = asyncevent.EventLoop()
        self.thread = concurrent.thread(self.loop.run_forever)
        self.thread.start()
        self.completed = threading.Event()

    def teardown_method(self, m):
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join()
        self.loop.close()

    def complete(self, result):
        self.completed.set()

    def test_running_stop_during_wait(self):
        checker = check.DirectioChecker(self.loop, "/path", self.complete)
        self.loop.call_soon_threadsafe(checker.start)
        assert self.completed.wait(1.0)
        self.loop.call_soon_threadsafe(checker.stop)
        assert checker.wait(1.0)
        assert not checker.is_running()

    @pytest.mark.slow
    def test_running_stop_during_check(self, fake_dd):
        fake_dd.configure(delay=0.2)
        checker = check.DirectioChecker(self.loop, "/path", self.complete)
        self.loop.call_soon_threadsafe(checker.start)
        self.loop.call_soon_threadsafe(checker.stop)
        assert checker.wait(1.0)
        assert not self.completed.is_set()
        assert not checker.is_running()

    @pytest.mark.slow
    def test_stopping_timeout(self, fake_dd):
        fake_dd.configure(delay=0.2)
        checker = check.DirectioChecker(self.loop, "/path", self.complete)
        self.loop.call_soon_threadsafe(checker.start)
        self.loop.call_soon_threadsafe(checker.stop)
        assert not checker.wait(0.1)
        assert checker.is_running()


class TestDirectioCheckerTimings:

    def setup_method(self):
        self.loop = asyncevent.EventLoop()
        self.results = []

    def teardown_method(self):
        self.loop.close()

    def complete(self, result):
        self.results.append(result)
        if len(self.results) == self.checkers:
            self.loop.stop()

    @pytest.mark.slow
    @pytest.mark.parametrize('checkers', [1, 50, 100, 200])
    def test_path_ok(self, checkers):
        self.checkers = checkers
        with temporaryPath(data=b"blah") as path:
            start = time.time()
            for i in range(checkers):
                checker = check.DirectioChecker(self.loop, path, self.complete)
                checker.start()
            self.loop.run_forever()
            elapsed = time.time() - start
            assert len(self.results) == self.checkers
            print("%d checkers: %f seconds" % (checkers, elapsed))
            # Make sure all succeeded
            for res in self.results:
                res.delay()

    @pytest.mark.slow
    @pytest.mark.parametrize('checkers', [1, 50, 100, 200])
    def test_path_missing(self, checkers):
        self.checkers = checkers
        start = time.time()
        for i in range(checkers):
            checker = check.DirectioChecker(self.loop, "/no/such/path",
                                            self.complete)
            checker.start()
        self.loop.run_forever()
        elapsed = time.time() - start
        assert len(self.results) == self.checkers
        print("%d checkers: %f seconds" % (checkers, elapsed))
        # Make sure all failed
        for res in self.results:
            with pytest.raises(exception.MiscFileReadException):
                res.delay()


class TestCheckService:

    def setup_method(self, m):
        self.service = check.CheckService()
        self.service.start()
        self.result = None
        self.completed = threading.Event()

    def teardown_method(self, m):
        self.service.stop()

    def complete(self, result):
        self.result = result
        self.completed.set()

    def test_start_checking(self, fake_dd):
        fake_dd.configure(delay=0.0)
        self.service.start_checking("/path", self.complete)
        assert self.service.is_checking("/path")
        assert self.completed.wait(1.0)
        assert self.result.rc == 0

    def test_start_checking_already_watched(self, fake_dd):
        fake_dd.configure(delay=0.0)
        self.service.start_checking("/path", self.complete)
        with pytest.raises(RuntimeError):
            self.service.start_checking("/path", self.complete)

    def test_stop_checking(self, fake_dd):
        fake_dd.configure(delay=0.0)
        self.service.start_checking("/path", self.complete)
        self.service.stop_checking("/path")
        assert not self.service.is_checking("/path")

    def test_stop_checking_not_watched(self):
        with pytest.raises(KeyError):
            self.service.stop_checking("/path")

    def test_stop_checking_and_wait(self, fake_dd):
        fake_dd.configure(delay=0.0)
        self.service.start_checking("/path", self.complete)
        assert self.service.stop_checking("/path", timeout=1.0)
        assert not self.service.is_checking("/path")

    @pytest.mark.slow
    def test_stop_checking_timeout(self, fake_dd):
        fake_dd.configure(delay=0.2)
        self.service.start_checking("/path", self.complete)
        assert not self.service.stop_checking("/path", timeout=0.1)
        assert not self.service.is_checking("/path")


@pytest.mark.parametrize('err, seconds', [
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
def test_check_result_success(err, seconds):
    result = check.CheckResult("/path", 0, err, 0, 0)
    assert result.delay() == seconds


def test_check_result_non_zero_exit_code():
    path = "/path"
    reason = "REASON"
    result = check.CheckResult(path, 1, reason, 0, 0)
    with pytest.raises(exception.MiscFileReadException) as ctx:
        result.delay()
    assert path in str(ctx.value)
    assert reason in str(ctx.value)


@pytest.mark.parametrize('err', [
    b"",
    b"1\n2\n\n",
    b"1\n2\n1024 bytes (1 kB) copied, BAD, 1 kB/s\n",
    b"1\n2\n1024 bytes (1 kB) copied, BAD s, 1 kB/s\n",
    b"1\n2\n1024 bytes (1 kB) copied, -1- s, 1 kB/s\n",
    b"1\n2\n1024 bytes (1 kB) copied, e3- s, 1 kB/s\n",
])
def test_unexpected_output(err):
    result = check.CheckResult("/path", 0, err, 0, 0)
    with pytest.raises(exception.MiscFileReadException):
        result.delay()


class FakeDD(object):
    def __init__(self, path):
        self._path = path
        self.configure(delay=0)
        os.chmod(self._path, 0o700)

    def configure(self, delay):
        rate = int(100 / delay) if delay else "Infinity"
        script_template = """#!/bin/sh
sleep {delay}
echo 0+1 records in >&2
echo 0+1 records out >&2
echo 100 bytes copied, {delay} s, {rate} B/s >&2
"""
        data = script_template.format(delay=delay, rate=rate)
        with open(self._path, "w") as f:
            f.write(data)


@pytest.fixture
def fake_dd(tmpdir, monkeypatch):
    path = str(tmpdir.join("fake-dd"))
    monkeypatch.setattr(constants, "EXT_DD", path)
    return FakeDD(path)
