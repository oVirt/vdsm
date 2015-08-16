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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#

import time
import random
import threading

from testlib import VdsmTestCase, expandPermutations, permutations
from testValidation import slowtest, stresstest

from vdsm import concurrent


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
        concurrent.tmap(time.sleep, [0.1] * 10)
        elapsed = time.time() - start
        self.assertTrue(0.1 < elapsed < 0.2)

    def test_error(self):
        error = RuntimeError("No result for you!")

        def func(x):
            raise error

        results = concurrent.tmap(func, range(10))
        expected = [concurrent.Result(False, error)] * 10
        self.assertEqual(results, expected)


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
