#
# Copyright 2015-2016 Red Hat, Inc.
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

from __future__ import absolute_import, print_function
import threading
import time

from testlib import VdsmTestCase
from testValidation import slowtest, stresstest
from testlib import expandPermutations, permutations
from testlib import start_thread, LockingThread

from vdsm import utils
from vdsm.concurrent import Barrier

# Temporary import of both implementations, to make sure that the test pass
# with both before we drop the old one and use the new.
from storage.misc import RWLock as OldRWLock
from vdsm.storage.rwlock import RWLock as NewRWLock


@expandPermutations
class RWLockTests(VdsmTestCase):

    @permutations([[OldRWLock], [NewRWLock]])
    def test_concurrent_readers(self, lock_class):
        lock = lock_class()
        readers = []
        try:
            for i in range(5):
                t = LockingThread(lock.shared)
                t.start()
                readers.append(t)
            for t in readers:
                self.assertTrue(t.acquired.wait(1))
        finally:
            for t in readers:
                t.stop()

    @slowtest
    @permutations([[OldRWLock], [NewRWLock]])
    def test_wakeup_blocked_writer(self, lock_class):
        lock = lock_class()
        reader = LockingThread(lock.shared)
        with utils.running(reader):
            if not reader.acquired.wait(2):
                raise RuntimeError("Timeout waiting for reader thread")
            writer = LockingThread(lock.exclusive)
            with utils.running(writer):
                if not writer.ready.wait(2):
                    raise RuntimeError("Timeout waiting for writer thread")
                self.assertFalse(writer.acquired.wait(1))
                reader.done.set()
                self.assertTrue(writer.acquired.wait(2))

    @slowtest
    @permutations([[OldRWLock], [NewRWLock]])
    def test_wakeup_blocked_reader(self, lock_class):
        lock = lock_class()
        writer = LockingThread(lock.exclusive)
        with utils.running(writer):
            if not writer.acquired.wait(2):
                raise RuntimeError("Timeout waiting for writer thread")
            reader = LockingThread(lock.shared)
            with utils.running(reader):
                if not reader.ready.wait(2):
                    raise RuntimeError("Timeout waiting for reader thread")
                self.assertFalse(reader.acquired.wait(1))
                writer.done.set()
                self.assertTrue(reader.acquired.wait(2))

    @slowtest
    @permutations([[OldRWLock], [NewRWLock]])
    def test_wakeup_all_blocked_readers(self, lock_class):
        lock = lock_class()
        readers = 10
        ready = Barrier(readers + 1)
        done = Barrier(readers + 1)
        threads = []

        def slow_reader():
            ready.wait(2)
            with lock.shared:
                time.sleep(0.5)
            done.wait(2)

        try:
            with lock.exclusive:
                # Start all readers
                for i in range(readers):
                    t = start_thread(slow_reader)
                    threads.append(t)
                # Wait until all readers are ready
                ready.wait(0.5)
                # Ensure that all readers are blocked
                time.sleep(0.5)
            # Releasing the write lock should wake up all the readers, holding
            # the lock for about 0.5 seconds.
            with self.assertNotRaises():
                done.wait(2)
        finally:
            for t in threads:
                t.join()

    @permutations([[OldRWLock], [NewRWLock]])
    def test_release_other_thread_write_lock(self, lock_class):
        lock = lock_class()
        writer = LockingThread(lock.exclusive)
        with utils.running(writer):
            if not writer.acquired.wait(2):
                raise RuntimeError("Timeout waiting for writer thread")
            self.assertRaises(RuntimeError, lock.release)

    @permutations([[OldRWLock], [NewRWLock]])
    def test_release_other_thread_read_lock(self, lock_class):
        lock = lock_class()
        reader = LockingThread(lock.shared)
        with utils.running(reader):
            if not reader.acquired.wait(2):
                raise RuntimeError("Timeout waiting for reader thread")
            self.assertRaises(RuntimeError, lock.release)

    @slowtest
    @permutations([[OldRWLock], [NewRWLock]])
    def test_fifo(self, lock_class):
        lock = lock_class()
        threads = []
        try:
            with lock.shared:
                writer = LockingThread(lock.exclusive)
                writer.start()
                threads.append(writer)
                if not writer.ready.wait(2):
                    raise RuntimeError("Timeout waiting for writer thread")
                # Writer must block
                self.assertFalse(writer.acquired.wait(1))
                reader = LockingThread(lock.shared)
                reader.start()
                threads.append(reader)
                if not reader.ready.wait(1):
                    raise RuntimeError("Timeout waiting for reader thread")
                # Reader must block
                self.assertFalse(reader.acquired.wait(1))
            # Writer should get in before the reader
            self.assertTrue(writer.acquired.wait(1))
            writer.done.set()
            self.assertTrue(reader.acquired.wait(1))
        finally:
            for t in threads:
                t.stop()

    @slowtest
    @permutations([[OldRWLock], [NewRWLock]])
    def test_shared_context_blocks_writer(self, lock_class):
        lock = lock_class()
        writer = LockingThread(lock.exclusive)
        try:
            with lock.shared:
                writer.start()
                if not writer.ready.wait(2):
                    raise RuntimeError("Timeout waiting for writer thread")
                # Writer must block
                self.assertFalse(writer.acquired.wait(1))
        finally:
            writer.stop()

    @permutations([[OldRWLock], [NewRWLock]])
    def test_shared_context_allows_reader(self, lock_class):
        lock = lock_class()
        with lock.shared:
            reader = LockingThread(lock.shared)
            with utils.running(reader):
                self.assertTrue(reader.acquired.wait(1))

    @slowtest
    @permutations([[OldRWLock], [NewRWLock]])
    def test_exclusive_context_blocks_writer(self, lock_class):
        lock = lock_class()
        writer = LockingThread(lock.exclusive)
        try:
            with lock.exclusive:
                writer.start()
                if not writer.ready.wait(2):
                    raise RuntimeError("Timeout waiting for writer thread")
                # Reader must block
                self.assertFalse(writer.acquired.wait(1))
        finally:
            writer.stop()

    @slowtest
    @permutations([[OldRWLock], [NewRWLock]])
    def test_exclusive_context_blocks_reader(self, lock_class):
        lock = lock_class()
        reader = LockingThread(lock.shared)
        try:
            with lock.exclusive:
                reader.start()
                if not reader.ready.wait(2):
                    raise RuntimeError("Timeout waiting for reader thread")
                # Reader must block
                self.assertFalse(reader.acquired.wait(1))
        finally:
            reader.stop()

    @permutations([[OldRWLock], [NewRWLock]])
    def test_recursive_write_lock(self, lock_class):
        lock = lock_class()
        with lock.exclusive:
            with lock.exclusive:
                pass

    @permutations([[OldRWLock], [NewRWLock]])
    def test_recursive_read_lock(self, lock_class):
        lock = lock_class()
        with lock.shared:
            with lock.shared:
                pass

    def test_demotion_forbidden(self):
        # This was allowed in older implementation, but was broken.
        lock = NewRWLock()
        with lock.exclusive:
            self.assertRaises(RuntimeError, lock.acquire_read)

    def test_promotion_forbidden_new(self):
        lock = NewRWLock()
        with lock.shared:
            self.assertRaises(RuntimeError, lock.acquire_write)

    def test_promotion_forbidden_old(self):
        lock = OldRWLock()
        with lock.shared:
            self.assertRaises(RuntimeError, lock.acquireWrite)


@expandPermutations
class RWLockStressTests(VdsmTestCase):

    @stresstest
    @permutations([
        (1, 2, OldRWLock),
        (1, 2, NewRWLock),
        (2, 8, OldRWLock),
        (2, 8, NewRWLock),
        (3, 32, OldRWLock),
        (3, 32, NewRWLock),
        (4, 128, OldRWLock),
        (4, 128, NewRWLock),
    ])
    def test_lock_contention(self, writers, readers, lock_class):
        lock = lock_class()
        ready = Barrier(writers + readers + 1)
        done = threading.Event()
        reads = [0] * readers
        writes = [0] * writers
        threads = []

        def read(slot):
            ready.wait()
            while not done.is_set():
                with lock.shared:
                    reads[slot] += 1

        def write(slot):
            ready.wait()
            while not done.is_set():
                with lock.exclusive:
                    writes[slot] += 1

        try:
            for i in range(readers):
                t = start_thread(read, i)
                threads.append(t)
            for i in range(writers):
                t = start_thread(write, i)
                threads.append(t)
            ready.wait(5)
            time.sleep(1)
        finally:
            done.set()
            for t in threads:
                t.join()

        print()
        print("writers: %d readers: %d" % (writers, readers))

        avg_writes, med_writes, min_writes, max_writes = stats(writes)
        print("writes  avg=%.2f med=%d min=%d max=%d"
              % (avg_writes, med_writes, min_writes, max_writes))

        avg_reads, med_reads, min_reads, max_reads = stats(reads)
        print("reads   avg=%.2f med=%d min=%d max=%d"
              % (avg_reads, med_reads, min_reads, max_reads))

    @stresstest
    @permutations([
        (1, OldRWLock),
        (1, NewRWLock),
        (2, OldRWLock),
        (2, NewRWLock),
        (4, OldRWLock),
        (4, NewRWLock),
        (8, OldRWLock),
        (8, NewRWLock),
        (16, OldRWLock),
        (16, NewRWLock),
        (32, OldRWLock),
        (32, NewRWLock),
        (64, OldRWLock),
        (64, NewRWLock),
        (128, OldRWLock),
        (128, NewRWLock),
    ])
    def test_readers(self, readers, lock_class):
        lock = lock_class()
        ready = Barrier(readers + 1)
        done = threading.Event()
        reads = [0] * readers
        threads = []

        def read(slot):
            ready.wait()
            while not done.is_set():
                with lock.shared:
                    reads[slot] += 1

        try:
            for i in range(readers):
                t = start_thread(read, i)
                threads.append(t)
            ready.wait(5)
            time.sleep(1)
        finally:
            done.set()
            for t in threads:
                t.join()

        print()
        avg_reads, med_reads, min_reads, max_reads = stats(reads)
        print("reads   avg=%.2f med=%d min=%d max=%d"
              % (avg_reads, med_reads, min_reads, max_reads))


def stats(seq):
    seq = sorted(seq)
    avg = sum(seq) / float(len(seq))
    med = seq[len(seq)/2]
    return avg, med, seq[0], seq[-1]
