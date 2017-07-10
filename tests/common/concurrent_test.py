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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#

import logging
import random
import threading
import time

from fakelib import FakeLogger
from testlib import VdsmTestCase, expandPermutations, permutations
from testValidation import slowtest, stresstest

from vdsm.common import concurrent


@expandPermutations
class BarrierTests(VdsmTestCase):

    def test_invalid_count(self):
        self.assertRaises(ValueError, concurrent.Barrier, 0)

    def test_last_thread(self):
        barrier = concurrent.Barrier(1)
        barrier.wait(timeout=0)

    def test_timeout(self):
        barrier = concurrent.Barrier(2)
        self.assertRaises(concurrent.Timeout, barrier.wait, 0.1)

    @slowtest
    def test_no_timeout(self):
        barrier = concurrent.Barrier(2)
        done = threading.Event()

        def waiter():
            barrier.wait()
            done.set()

        t = threading.Thread(target=waiter)
        t.daemon = True
        t.start()
        barrier.wait()
        self.assertTrue(done.wait(timeout=0.5))

    @slowtest
    def test_block_thread(self):
        barrier = concurrent.Barrier(2)
        done = threading.Event()

        def waiter():
            barrier.wait(timeout=1)
            done.set()

        t = threading.Thread(target=waiter)
        t.daemon = True
        t.start()
        try:
            self.assertFalse(done.wait(timeout=0.5))
        finally:
            barrier.wait(timeout=0)
            t.join()

    @slowtest
    def test_wake_up_blocked_thread(self):
        barrier = concurrent.Barrier(2)
        done = threading.Event()

        def waiter():
            barrier.wait(timeout=2)
            done.set()

        t = threading.Thread(target=waiter)
        t.daemon = True
        t.start()
        try:
            if done.wait(timeout=0.5):
                raise RuntimeError("Thread did not block")
            barrier.wait(timeout=0)
            self.assertTrue(done.wait(timeout=0.5))
        finally:
            t.join()

    @slowtest
    def test_wake_up_exactly_count_threads(self):
        barrier = concurrent.Barrier(2)
        lock = threading.Lock()
        done = [0]

        def waiter():
            barrier.wait(timeout=2)
            with lock:
                done[0] += 1

        threads = []
        for i in range(3):
            t = threading.Thread(target=waiter)
            t.daemon = True
            t.start()
            threads.append(t)
        try:
            time.sleep(0.5)
            # The first 2 threads waiting should be done now
            self.assertEqual(done[0], 2)
            # This should wake up the last thread waiting
            barrier.wait(timeout=0)
            time.sleep(0.5)
            self.assertEqual(done[0], 3)
        finally:
            for t in threads:
                t.join()

    @stresstest
    @permutations([[2], [4], [8], [16], [32], [64], [128], [256]])
    def test_multiple_threads(self, count):
        timeout = 5.0
        # Wait for count threads + test thread
        barrier = concurrent.Barrier(count + 1)
        threads = []

        def waiter():
            time.sleep(0.1)
            barrier.wait(timeout=timeout)

        try:
            # Start count threads waiting on the barrier
            for i in range(count):
                t = threading.Thread(target=waiter)
                t.daemon = True
                t.start()
                threads.append(t)
            # Wait until all threads entered the barrier. Timeout is considerd
            # a failure.
            with self.assertNotRaises():
                barrier.wait(timeout=timeout)
        finally:
            for t in threads:
                t.join()


class TMapTests(VdsmTestCase):

    def test_results(self):
        values = tuple(range(10))
        results = concurrent.tmap(lambda x: x, values)
        expected = [concurrent.Result(True, x) for x in values]
        self.assertEqual(results, expected)

    def test_results_order(self):
        def func(x):
            time.sleep(x)
            return x
        values = tuple(random.random() * 0.1 for x in range(10))
        results = concurrent.tmap(func, values)
        expected = [concurrent.Result(True, x) for x in values]
        self.assertEqual(results, expected)

    def test_concurrency(self):
        start = time.time()
        concurrent.tmap(time.sleep, [0.5] * 10)
        elapsed = time.time() - start
        self.assertGreater(elapsed, 0.5)
        self.assertLess(elapsed, 1.0)

    def test_error(self):
        error = RuntimeError("No result for you!")

        def func(x):
            raise error

        results = concurrent.tmap(func, range(10))
        expected = [concurrent.Result(False, error)] * 10
        self.assertEqual(results, expected)


@expandPermutations
class ThreadTests(VdsmTestCase):

    def test_run_callable_in_thread(self):
        self.thread = threading.current_thread()

        def run():
            self.thread = threading.current_thread()

        t = concurrent.thread(run)
        t.start()
        t.join()
        self.assertEqual(t, self.thread)

    def test_default_daemon_thread(self):
        t = concurrent.thread(lambda: None)
        t.start()
        try:
            self.assertTrue(t.daemon)
        finally:
            t.join()

    def test_non_daemon_thread(self):
        t = concurrent.thread(lambda: None, daemon=False)
        t.start()
        try:
            self.assertFalse(t.daemon)
        finally:
            t.join()

    def test_name(self):
        t = concurrent.thread(lambda: None, name="foobar")
        self.assertEqual("foobar", t.name)

    def test_pass_args(self):
        self.args = ()

        def run(*args):
            self.args = args

        t = concurrent.thread(run, args=(1, 2, 3))
        t.start()
        t.join()
        self.assertEqual((1, 2, 3), self.args)

    def test_pass_kwargs(self):
        self.kwargs = ()

        def run(**kwargs):
            self.kwargs = kwargs

        kwargs = {'a': 1, 'b': 2}
        t = concurrent.thread(run, kwargs=kwargs)
        t.start()
        t.join()
        self.assertEqual(kwargs, self.kwargs)

    def test_pass_args_and_kwargs(self):
        self.args = ()
        self.kwargs = {}

        def run(*args, **kwargs):
            self.args = args
            self.kwargs = kwargs

        args = (1, 2)
        kwargs = {'a': 3, 'b': 4}
        t = concurrent.thread(run, args=args, kwargs=kwargs)
        t.start()
        t.join()
        self.assertEqual(args, self.args)
        self.assertEqual(kwargs, self.kwargs)

    def test_log_success(self):
        log = FakeLogger()

        def run():
            log.debug("Threads are cool")

        t = concurrent.thread(run, log=log)
        t.start()
        t.join()

        level, message, kwargs = log.messages[0]
        self.assertEqual(level, logging.DEBUG)
        self.assertTrue(message.startswith("START thread"),
                        "Unxpected message: %s" % message)
        self.assertEqual(kwargs, {})

        self.assertEqual(log.messages[1],
                         (logging.DEBUG, "Threads are cool", {}))

        level, message, kwargs = log.messages[2]
        self.assertEqual(level, logging.DEBUG)
        self.assertTrue(message.startswith("FINISH thread"),
                        "Unxpected message: %s" % message)
        self.assertEqual(kwargs, {})

    @permutations([
        (RuntimeError,),
        (GeneratorExit,),
        (BaseException,),
    ])
    def test_log_failure(self, exc_class):
        def run():
            raise exc_class("Threads are evil")

        log = FakeLogger()
        t = concurrent.thread(run, log=log)
        t.start()
        t.join()

        level, message, kwargs = log.messages[0]
        self.assertEqual(level, logging.DEBUG)
        self.assertTrue(message.startswith("START thread"),
                        "Unxpected message: %s" % message)

        level, message, kwargs = log.messages[1]
        self.assertEqual(level, logging.ERROR)
        self.assertTrue(message.startswith("FINISH thread"),
                        "Unxpected message: %s" % message)
        self.assertEqual(kwargs, {"exc_info": True})

    @permutations([
        (SystemExit,),
        (KeyboardInterrupt,),
    ])
    def test_log_expected_exceptions(self, exc_class):
        def run():
            raise exc_class("Don't panic")

        log = FakeLogger()
        t = concurrent.thread(run, log=log)
        t.start()
        t.join()

        level, message, kwargs = log.messages[0]
        self.assertEqual(level, logging.DEBUG)
        self.assertTrue(message.startswith("START thread"),
                        "Unxpected message: %s" % message)

        level, message, kwargs = log.messages[1]
        self.assertEqual(level, logging.DEBUG)
        self.assertTrue(message.startswith("FINISH thread"),
                        "Unxpected message: %s" % message)
        self.assertIn("Don't panic", message)
        self.assertEqual(kwargs, {})


class TestValidatingEvent(VdsmTestCase):

    def test_create(self):
        event = concurrent.ValidatingEvent()
        self.assertFalse(event.is_set(), "Event is set")
        self.assertTrue(event.valid, "Event is invalid")

    def test_set(self):
        event = concurrent.ValidatingEvent()
        event.set()
        self.assertTrue(event.is_set(), "Event is not set")
        self.assertTrue(event.valid, "Event is invalid")

    def test_clear(self):
        event = concurrent.ValidatingEvent()
        event.set()
        event.clear()
        self.assertFalse(event.is_set(), "Event is set")
        self.assertTrue(event.valid, "Event is invalid")

    def test_wait_timeout(self):
        event = concurrent.ValidatingEvent()
        self.assertFalse(event.wait(0), "Event did not timed out")
        self.assertTrue(event.valid, "Event is invalid")

    def test_wait_already_set(self):
        event = concurrent.ValidatingEvent()
        event.set()
        self.assertTrue(event.wait(1), "Timeout on set event")
        self.assertTrue(event.valid, "Event is invalid")

    def test_set_wake_up_waiters(self):
        count = 3
        event = concurrent.ValidatingEvent()
        ready = concurrent.Barrier(count + 1)
        woke_up = [False] * count

        def wait(n):
            ready.wait(1)
            woke_up[n] = event.wait(1)

        threads = []
        try:
            for i in range(count):
                t = concurrent.thread(wait, args=(i,))
                t.start()
                threads.append(t)
            # Wait until all threads entered the barrier.
            ready.wait(1)
            # Give threads time to enter the event.
            time.sleep(0.5)
            event.set()
        finally:
            for t in threads:
                t.join()

        self.assertTrue(all(woke_up),
                        "Some threads did not wake up: %s" % woke_up)
        self.assertTrue(event.valid, "Event is invalid")

    def test_wait_on_invalid_event(self):
        event = concurrent.ValidatingEvent()
        event.valid = False
        with self.assertRaises(concurrent.InvalidEvent):
            event.wait(1)
        self.assertFalse(event.valid, "Event is valid")

    def test_invalidate_wake_up_waiters(self):
        count = 3
        event = concurrent.ValidatingEvent()
        ready = concurrent.Barrier(count + 1)
        invalidated = [False] * count

        def wait(n):
            ready.wait(1)
            try:
                event.wait(1)
            except concurrent.InvalidEvent:
                invalidated[n] = True

        threads = []
        try:
            for i in range(count):
                t = concurrent.thread(wait, args=(i,))
                t.start()
                threads.append(t)
            # Wait until all threads entered the barrier.
            ready.wait(1)
            # Give threads time to enter the event.
            time.sleep(0.5)
            event.valid = False
        finally:
            for t in threads:
                t.join()

        self.assertTrue(all(invalidated),
                        "Some threads were no invalidated: %s" % invalidated)
        self.assertFalse(event.valid, "Event is valid")
