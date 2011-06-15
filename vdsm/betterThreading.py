# Copyright 2011 Red Hat, Inc. and/or its affiliates.
#
# Licensed to you under the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.  See the files README and
# LICENSE_GPL_v2 which accompany this distribution.
#

"""
betterThreading module provides Lock, Condition and Event synchronization
objects compatible with Python native threading module.
The implementation, however, is based on POSIX thread library as delivered
by the libpthread. Lock and Condition are designed to be a drop-in
replacement for their respective threading counterpart, and Event is a
verbatim copy of that of the threading module.
"""

import time
import errno
import os

import pthread

class Lock(pthread.PthreadMutex):
    """
    Lock class mimics Python native threading.Lock() API on top of
    the POSIX thread mutex synchronization primitive.
    """
    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.release()

    def acquire(self, blocking=True):

        rc = self.lock() if blocking else self.trylock()

        if rc == 0:
            return True
        elif rc == errno.EBUSY:
            return False
        else:
            raise OSError(rc, os.strerror(rc))

    def release(self):
        self.unlock()


class Condition(object):
    """
    Condition class mimics Python native threading.Condition() API
    on top of the POSIX thread conditional variable synchronization
    primitive.
    """
    def __init__(self, lock=None):
        self.__lock = lock if lock else Lock()
        self.__cond = pthread.PthreadCond(mutex=self.__lock)
        # Export the lock's acquire() and release() methods
        self.acquire = self.__lock.acquire
        self.release = self.__lock.release

    def __enter__(self):
        return self.__lock.__enter__()

    def __exit__(self, *args):
        return self.__lock.__exit__(*args)

    def wait(self, timeout=None):
        if timeout is not None:
            bailout = time.time() + timeout
            abstime = pthread.timespec()
            abstime.tv_sec = int(bailout)
            abstime.tv_nsec = int((bailout - int(bailout)) * (10**9))
            return self.__cond.timedwait(abstime)
        else:
            return self.__cond.wait()

    def notify(self):
        return self.__cond.signal()

    def notifyAll(self):
        return self.__cond.broadcast()

    notify_all = notifyAll


#
# This class is copied verbatim from threading.py since it is
# a convenience wrapper around Condition and I didn't feel like
# re-inventing the wheel.
#
class Event(object):

    # After Tim Peters' event class (without is_posted())

    def __init__(self):
        self.__cond = Condition(Lock())
        self.__flag = False

    def isSet(self):
        return self.__flag

    is_set = isSet

    def set(self):
        self.__cond.acquire()
        try:
            self.__flag = True
            self.__cond.notify_all()
        finally:
            self.__cond.release()

    def clear(self):
        self.__cond.acquire()
        try:
            self.__flag = False
        finally:
            self.__cond.release()

    def wait(self, timeout=None):
        self.__cond.acquire()
        try:
            if not self.__flag:
                self.__cond.wait(timeout)
            return self.__flag
        finally:
            self.__cond.release()

# hack threading module to use our classes, so that Queue and SocketServer can
# easily enjoy them.

import threading

threading.Condition = Condition
threading.Lock = Lock
