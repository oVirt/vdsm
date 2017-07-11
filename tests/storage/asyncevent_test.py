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

import asyncore
import os
import random
import socket
import subprocess
import time
from contextlib import closing

import pytest

from testlib import VdsmTestCase
from testlib import expandPermutations, permutations

from vdsm.common import concurrent
from vdsm.common import osutils
import vdsm.common.time
from vdsm.storage import asyncevent


class TestEventLoop(VdsmTestCase):

    def setUp(self):
        self.loop = asyncevent.EventLoop()

    def tearDown(self):
        self.loop.close()

    def test_stop_while_running(self):
        self.was_called = False

        def callback():
            self.was_called = True
            self.loop.stop()

        self.loop.call_soon(callback)
        self.loop.run_forever()
        self.assertTrue(self.was_called)

    def test_stop_before_running(self):
        self.was_called = False

        def callback():
            self.was_called = True

        self.loop.call_soon(callback)
        self.loop.stop()
        self.loop.run_forever()
        self.assertTrue(self.was_called)

    def test_stop_abort_call_later(self):
        self.was_called = False

        def callback():
            self.was_called = True

        self.loop.call_later(0, callback)
        self.loop.stop()
        self.loop.run_forever()
        self.assertFalse(self.was_called)

    def test_stop_abort_call_soon(self):
        self.was_called = False

        def callback():
            self.was_called = True

        self.loop.stop()
        self.loop.call_soon(0, callback)
        self.loop.run_forever()
        self.assertFalse(self.was_called)

    def test_stop_keep_call_soon(self):
        self.calls = []

        def callback(n):
            self.calls.append(n)

        self.loop.call_soon(callback, 1)
        self.loop.stop()
        self.loop.call_soon(callback, 2)
        self.loop.stop()

        self.loop.run_forever()
        self.assertEqual([1], self.calls)

        self.loop.run_forever()
        self.assertEqual([1, 2], self.calls)

    def test_stop_keep_call_later(self):
        self.calls = []

        def callback(n):
            self.calls.append(n)

        self.loop.call_later(0.0, callback, 1)
        self.loop.call_later(0.1, self.loop.stop)
        self.loop.call_later(0.2, callback, 2)
        self.loop.call_later(0.3, self.loop.stop)

        self.loop.run_forever()
        self.assertEqual([1], self.calls)

        self.loop.run_forever()
        self.assertEqual([1, 2], self.calls)

    def test_is_running_before(self):
        self.assertFalse(self.loop.is_running())

    def test_is_running_during(self):
        self.was_running = False

        def callback():
            self.was_running = self.loop.is_running()
            self.loop.stop()

        self.loop.call_soon(callback)
        self.loop.run_forever()
        self.assertTrue(self.was_running)

    def test_is_running_after(self):
        self.loop.stop()
        self.loop.run_forever()
        self.assertFalse(self.loop.is_running())

    def test_fail_when_running_run_forever(self):
        self.error = None

        def callback():
            try:
                self.loop.run_forever()
            except Exception as e:
                self.error = e
            self.loop.stop()

        self.loop.call_soon(callback)
        self.loop.run_forever()
        self.assertEqual(type(self.error), RuntimeError)

    def test_fail_when_running_close(self):
        self.error = None

        def callback():
            try:
                self.loop.close()
            except Exception as e:
                self.error = e
            self.loop.stop()

        self.loop.call_soon(callback)
        self.loop.run_forever()
        self.assertEqual(type(self.error), RuntimeError)

    def test_fail_when_closed_call_soon(self):
        self.loop.close()
        self.assertRaises(RuntimeError, self.loop.call_soon, lambda: None)

    def test_fail_when_closed_call_later(self):
        self.loop.close()
        self.assertRaises(RuntimeError, self.loop.call_later, 0, lambda: None)

    def test_fail_when_closed_call_at(self):
        self.loop.close()
        self.assertRaises(RuntimeError, self.loop.call_at, self.loop.time(),
                          lambda: None)

    def test_is_closed_before(self):
        self.loop.stop()
        self.loop.run_forever()
        self.assertFalse(self.loop.is_closed())

    def test_is_closed_after(self):
        self.loop.stop()
        self.loop.run_forever()
        self.loop.close()
        self.assertTrue(self.loop.is_closed())

    def test_close_twice(self):
        self.loop.close()
        self.loop.close()

    def test_call_soon_failure(self):
        self.failed = False
        self.count = 0

        def fail():
            self.failed = True
            raise RuntimeError("Expected failure")

        def callback():
            self.count += 1
            self.loop.stop()

        self.loop.call_soon(fail)
        self.loop.call_soon(callback)
        self.loop.run_forever()
        self.assertTrue(self.failed)
        self.assertEqual(1, self.count)

    def test_call_soon_stop(self):
        self.was_called = False

        def callback():
            self.was_called = True

        self.loop.call_soon(callback)
        self.loop.call_soon(self.loop.stop)
        self.loop.run_forever()
        self.assertTrue(self.was_called)

    def test_call_soon_cancel(self):
        self.was_called = False

        def callback():
            self.was_called = True

        handle = self.loop.call_soon(callback)
        self.loop.call_soon(self.loop.stop)
        handle.cancel()
        self.loop.run_forever()
        self.assertFalse(self.was_called)

    def test_call_later_failure(self):
        self.failed = False
        self.count = 0

        def fail():
            self.failed = True
            raise RuntimeError("Expected failure")

        def callback():
            self.count += 1
            self.loop.stop()

        self.loop.call_later(0.00, fail)
        self.loop.call_later(0.01, callback)
        self.loop.run_forever()
        self.assertTrue(self.failed)
        self.assertEqual(1, self.count)

    def test_call_later_order(self):
        # event based sleep sort
        self.calls = []

        def callback(arg):
            self.calls.append(arg)

        self.loop.call_later(0.3, callback, 3)
        self.loop.call_later(0.1, callback, 1)
        self.loop.call_later(0.4, self.loop.stop)
        self.loop.call_later(0.0, callback, 0)
        self.loop.call_later(0.2, callback, 2)
        self.loop.run_forever()
        self.assertEqual([0, 1, 2, 3], self.calls)

    def test_call_later_stop(self):
        self.was_called = False

        def callback():
            self.was_called = True

        self.loop.call_later(0, callback)
        self.loop.call_soon(self.loop.stop)
        self.loop.run_forever()
        self.assertTrue(self.was_called)

    def test_call_later_cancel(self):
        self.was_called = False

        def callback():
            self.was_called = True

        handle = self.loop.call_later(0, callback)
        self.loop.call_soon(self.loop.stop)
        handle.cancel()
        self.loop.run_forever()
        self.assertFalse(self.was_called)

    def test_handle_error_failures(self):

        class EvilDispatcher(Echo):

            def handle_read(self):
                Echo.handle_read(self)
                raise Exception("Expected error")

            def handle_error(self):
                # This is a very big anti-pattern for dispatchers,
                # asyncore.poll2 will raise errors raised from handle_error.
                raise Exception("Evil error")

        def pinger(sock):
            msg = b"ping"
            osutils.uninterruptible(sock.send, msg)
            osutils.uninterruptible(sock.recv, len(msg))
            sock.close()
            self.loop.call_soon_threadsafe(self.loop.stop)

        sock1, sock2 = socket.socketpair()
        # The dispatcher and pinger owns the sockets
        self.loop.create_dispatcher(EvilDispatcher, sock2)
        t = concurrent.thread(pinger, args=(sock1,))
        t.start()
        try:
            # Correct error handling willl allow this test to complete without
            # errors. This used to abort the event loop with the error raised
            # in handle_error.
            self.loop.run_forever()
        finally:
            t.join()


@expandPermutations
class TestEventLoopTiming(VdsmTestCase):

    def setUp(self):
        self.loop = asyncevent.EventLoop()

    def tearDown(self):
        self.loop.close()

    @pytest.mark.slow
    @permutations([[1], [100], [1000]])
    def test_call_soon_loop(self, max_count):
        self.count = 0

        def callback(i):
            self.count = i
            if i == max_count:
                self.loop.stop()
                return
            self.loop.call_soon(callback, i + 1)

        start = time.time()
        self.loop.call_soon(callback, 1)
        self.loop.run_forever()
        elapsed = time.time() - start
        print("%7d loops: %f" % (max_count, elapsed))
        self.assertEqual(max_count, self.count)

    @pytest.mark.slow
    @permutations([[1], [100], [1000]])
    def test_call_soon_counters(self, counters):
        max_count = 100
        self.counts = [0] * counters

        def callback(index):
            self.counts[index] += 1
            if self.counts[index] == max_count:
                self.loop.stop()
                return
            self.loop.call_soon(callback, index)

        start = time.time()
        for i in range(counters):
            self.loop.call_soon(callback, i)
        self.loop.run_forever()
        elapsed = time.time() - start
        print("%7d counters: %f" % (counters, elapsed))
        self.assertEqual([max_count] * counters, self.counts)

    @pytest.mark.slow
    @permutations([[1], [100], [1000]])
    def test_call_later_loop(self, max_count):
        self.count = 0

        def callback(i):
            self.count = i
            if i == max_count:
                self.loop.stop()
                return
            self.loop.call_later(0, callback, i + 1)

        start = time.time()
        self.loop.call_later(0, callback, 0)
        self.loop.run_forever()
        elapsed = time.time() - start
        print("%7d loops: %f" % (max_count, elapsed))
        self.assertEqual(max_count, self.count)

    @pytest.mark.slow
    @permutations([[1], [100], [1000]])
    def test_call_later_counters(self, counters):
        max_count = 100
        self.counts = [0] * counters

        def callback(index):
            self.counts[index] += 1
            if self.counts[index] == max_count:
                self.loop.stop()
                return
            self.loop.call_later(0, callback, index)

        start = time.time()
        for i in range(counters):
            self.loop.call_later(0, callback, i)
        self.loop.run_forever()
        elapsed = time.time() - start
        print("%7d counters: %f" % (counters, elapsed))
        self.assertEqual([max_count] * counters, self.counts)

    @pytest.mark.slow
    @permutations([[1], [100], [1000], [10000], [100000]])
    def test_call_at(self, calls):
        start = time.time()
        now = self.loop.time()
        for i in range(calls):
            deadline = now + random.random()
            self.loop.call_at(deadline, None)
        elapsed = time.time() - start
        print("%7d calls: %f" % (calls, elapsed))

    # The event loop uses a monotonic clock with very low resolution (0.01
    # seconds). For this test it is useful to use a real time source with
    # microsecond resolution.
    @pytest.mark.slow
    @permutations([
        (vdsm.common.time.monotonic_time, 1),
        (vdsm.common.time.monotonic_time, 100),
        (vdsm.common.time.monotonic_time, 1000),
        (time.time, 1),
        (time.time, 100),
        (time.time, 1000),
    ])
    def test_call_at_latency(self, clock, timers):
        self.loop.time = clock
        interval = 0.001
        latency = [None] * timers

        def callback(index, deadline):
            latency[index] = self.loop.time() - deadline

        deadline = self.loop.time() + interval
        for i in range(timers):
            self.loop.call_at(deadline, callback, i, deadline)
            deadline += interval

        self.loop.call_at(deadline, self.loop.stop)
        self.loop.run_forever()
        latency.sort()
        min_lat = min(latency)
        avg_lat = sum(latency) / len(latency)
        med_lat = latency[len(latency) // 2 - 1]
        max_lat = max(latency)
        print("avg=%f, min=%f, med=%f, max=%f" %
              (avg_lat, min_lat, med_lat, max_lat))
        self.assertTrue(avg_lat < 0.01)

    @pytest.mark.slow
    @permutations([[1], [10], [1000], [10000]])
    def test_call_soon_threadsafe(self, calls):
        self.count = 0

        def callback():
            self.count += 1

        start = time.time()
        t = concurrent.thread(self.loop.run_forever)
        t.start()
        try:
            for i in range(calls):
                self.loop.call_soon_threadsafe(callback)
        finally:
            self.loop.call_soon_threadsafe(self.loop.stop)
            t.join()
        elapsed = time.time() - start
        print("%7d calls: %f seconds" % (calls, elapsed))
        self.assertEqual(calls, self.count)

    @pytest.mark.slow
    @permutations([[1], [100], [400]])
    def test_echo(self, concurrency):
        msg = b"ping"
        sockets = []
        try:
            for i in range(concurrency):
                sock1, sock2 = socket.socketpair()
                self.loop.create_dispatcher(Echo, sock2)
                sockets.append(sock1)
            t = concurrent.thread(self.loop.run_forever)
            t.start()
            try:
                start = time.time()
                for sock in sockets:
                    osutils.uninterruptible(sock.send, msg)
                for sock in sockets:
                    data = osutils.uninterruptible(sock.recv, len(msg))
                    self.assertEqual(data, msg)
                elapsed = time.time() - start
                print("%7d echos: %f seconds" % (concurrency, elapsed))
            finally:
                self.loop.call_soon_threadsafe(self.loop.stop)
                t.join()
        finally:
            for sock in sockets:
                sock.close()


class Echo(asyncore.dispatcher):

    def handle_read(self):
        data = osutils.uninterruptible(self.socket.recv, 4096)
        osutils.uninterruptible(self.socket.send, data)

    def writable(self):
        return False


@expandPermutations
class TestBufferedReader(VdsmTestCase):

    def setUp(self):
        self.loop = asyncevent.EventLoop()
        self.received = None

    def tearDown(self):
        self.loop.close()

    def complete(self, data):
        self.received = data
        self.loop.stop()

    @permutations([
        # size, bufsize
        (0, 1),
        (1, 32),
        (1024, 256),
        (4096, 1024),
        (16384, 4096),
        (65536, 16384),
    ])
    def test_read(self, size, bufsize):
        data = b"x" * size
        r, w = os.pipe()
        reader = self.loop.create_dispatcher(
            asyncevent.BufferedReader, r, self.complete, bufsize=bufsize)
        with closing(reader):
            os.close(r)  # Dupped by BufferedReader
            Sender(self.loop, w, data, bufsize)
            self.loop.run_forever()
            self.assertEqual(self.received, data)

    def test_complete_failure(self):
        complete_calls = [0]

        def failing_complete(data):
            complete_calls[0] += 1
            self.loop.stop()
            raise Exception("Complete failure!")

        data = b"it works"
        r, w = os.pipe()
        reader = self.loop.create_dispatcher(
            asyncevent.BufferedReader, r, failing_complete)
        with closing(reader):
            os.close(r)  # Dupped by BufferedReader
            Sender(self.loop, w, data, 64)
            self.loop.run_forever()

        # Complete must be called exactly once.
        self.assertEqual(complete_calls[0], 1)


class Sender(object):

    def __init__(self, loop, fd, data, bufsize):
        self.loop = loop
        self.fd = fd
        self.data = data
        self.pos = 0
        self.bufsize = bufsize
        self.loop.call_soon(self.send)

    def send(self):
        if self.pos == len(self.data):
            os.close(self.fd)
            return
        buf = memoryview(self.data)[self.pos:self.pos + self.bufsize]
        self.pos += os.write(self.fd, buf)
        self.loop.call_soon(self.send)


@expandPermutations
class TestReaper(VdsmTestCase):

    def setUp(self):
        self.loop = asyncevent.EventLoop()
        self.rc = None

    def tearDown(self):
        self.loop.close()

    def complete(self, rc):
        self.rc = rc
        self.loop.stop()

    def test_success(self):
        self.reap(["true"])
        self.assertEqual(0, self.rc)

    def test_failure(self):
        self.reap(["false"])
        self.assertEqual(1, self.rc)

    @pytest.mark.slow
    @permutations([[0.1], [0.2], [0.4], [0.8], [1.6]])
    def test_slow(self, delay):
        start = time.time()
        self.reap(["sleep", "%.1f" % delay])
        reap_time = time.time() - start - delay
        print("reap time: %.3f" % reap_time)
        self.assertLess(reap_time, 1.0)

    def reap(self, cmd):
        proc = subprocess.Popen(cmd, stdin=None, stdout=None, stderr=None)
        asyncevent.Reaper(self.loop, proc, self.complete)
        self.loop.run_forever()
