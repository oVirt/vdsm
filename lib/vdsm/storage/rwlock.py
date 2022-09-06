# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
import threading


class RWLock(object):
    """
    A simple readers-writer lock implementation.

    This lock is not preferring writers or readers. Each acquire request is put
    into a queue, and will be served in the order of the request.

    To acquire a write lock, use acquire_write(), followed by release() when
    you are done::

        lock.acquire_write()

        do stuff that require an exclusive lock..

        lock.release()

    If possible, use the RWLock.exclusive contextmanager::

        with lock.exclusive:
            do stuff that require an exclusive lock..

    When a thread is holding a write lock, other threads requesting a write or
    read lock will be blocked. When you release the write lock, the waiting
    threads are served in the order they requested the lock.

    To acquire a read lock, use acquire_read(), followed by release() when you
    are done::

        lock.acquire_read()

        do stuff that require a shared lock..

        lock.release()

    If possible, use the RWLock.shared contextmanager::

        with lock.shared:
            do stuff that require a shared lock..

    When a thread is holding a read lock, other threads requesting a read lock
    can acquire the lock. Other threads requesting a write lock will be blocked
    until all the readers release their lock.

    If a reader try to acquire a read lock while other threads are waiting
    (e.g. a writer), the reader is blocked until the waiting writer will get
    the lock.

    Recursive locking (either write or read lock) is allowed, to support old
    storage code locking same resource from different layers.

    Lock promotion or demotion is forbidden and will raise RuntimeError.
    """

    def __init__(self):
        self.shared = Context(self.acquire_read, self.release)
        self.exclusive = Context(self.acquire_write, self.release)
        self._lock = threading.Lock()
        self._waiters = []
        self._holders = {}
        self._writer = None

    def acquire_write(self):
        me = threading.current_thread()
        if me is self._writer:
            self._holders[me] += 1
            return
        if me in self._holders:
            raise RuntimeError("Lock promotion is forbidden")
        with self._lock:
            if self._holders or self._waiters:
                self._wait(True)
            self._holders[me] = 1
            self._writer = me

    def acquire_read(self):
        me = threading.current_thread()
        if me is self._writer:
            raise RuntimeError("Lock demotion is forbidden")
        if me in self._holders:
            self._holders[me] += 1
            return
        with self._lock:
            if self._writer or self._waiters:
                self._wait(False)
            self._holders[me] = 1
            if self._waiters:
                self._grant_next_waiter()

    def release(self):
        me = threading.current_thread()
        if me not in self._holders:
            raise RuntimeError("Thread %s attempted to release a lock it "
                               "does not hold" % me)
        self._holders[me] -= 1
        if self._holders[me] > 0:
            return
        with self._lock:
            self._writer = None
            del self._holders[me]
            if self._waiters:
                self._grant_next_waiter()

    def _wait(self, wants_write):
        waiter = Waiter(wants_write)
        self._waiters.append(waiter)
        try:
            self._lock.release()
            try:
                waiter.wait()
            finally:
                self._lock.acquire()
        finally:
            self._waiters.remove(waiter)

    def _grant_next_waiter(self):
        if self._holders and self._waiters[0].wants_write:
            return
        self._waiters[0].grant()


class Waiter(object):

    def __init__(self, wants_write):
        self.wants_write = wants_write
        self._event = threading.Event()

    def wait(self):
        self._event.wait()

    def grant(self):
        self._event.set()


class Context(object):

    def __init__(self, acquire, release):
        self._acquire = acquire
        self._release = release

    def __enter__(self):
        self._acquire()
        return self

    def __exit__(self, *args):
        self._release()
