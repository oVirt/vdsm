#
# Copyright 2011 Red Hat, Inc.
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

"""
pthread provides a Python bindings for POSIX thread synchronization
primitives. It implements mutex and conditional variable right now,
but can be easily extended to include also spin locks, rwlocks and
barriers if needed. It also does not implement non default mutex/condvar
attributes, which also can be added if required.
"""

import os
import ctypes as C

# This is the POSIX thread library. If we ever will need to use something else,
# we just need to redfine it here.

LIBPTHREAD = "libpthread.so.0"

# These come from pthread.h (via bits/pthreadtypes.h)
# We prefer to be on a safe side and use sizes for 64 bit implementation

SIZEOF_MUTEX_T = 40
SIZEOF_COND_T = 48
SIZEOF_MUTEXATTR_T = 4

PTHREAD_MUTEX_RECURSIVE = 1

MUTEX_T = C.c_char * SIZEOF_MUTEX_T
COND_T = C.c_char * SIZEOF_COND_T
MUTEXATTR_T = C.c_char * SIZEOF_MUTEXATTR_T

# This work well for Linux, but will fail on other OSes, where pthread library
# may have other name. So this module is not cross-platform.

_libpthread = C.CDLL(LIBPTHREAD, use_errno=True)


class timespec(C.Structure):
    _fields_ = [("tv_sec", C.c_long),
                ("tv_nsec", C.c_long)]


class mutexattr_t(C.Union):
    _fields_ = [("__size", MUTEXATTR_T),
                ("__align", C.c_int)]


class PthreadMutex(object):
    __slots__ = ("_mutex")

    def __init__(self, recursive=False):
        self._mutex = MUTEX_T()

        if recursive:
            attr = C.byref(mutexattr_t())
            _libpthread.pthread_mutexattr_settype(
                        attr, C.c_int(PTHREAD_MUTEX_RECURSIVE))
        else:
            attr = None

        res = _libpthread.pthread_mutex_init(self._mutex, attr)

        if res:
            raise OSError(res, os.strerror(res))

    def __del__(self):
        try:
            _libpthread.pthread_mutex_destroy(self._mutex)
        except AttributeError:
            if _libpthread is not None:
                raise

    def mutex(self):
        return self._mutex

    def lock(self):
        return _libpthread.pthread_mutex_lock(self._mutex)

    def unlock(self):
        return _libpthread.pthread_mutex_unlock(self._mutex)

    def trylock(self):
        return _libpthread.pthread_mutex_trylock(self._mutex)


class PthreadCond(object):
    __slots__ = ("_lock", "_cond")

    def __init__(self, mutex=None):
        self._cond = COND_T()
        self._lock = mutex
        res = _libpthread.pthread_cond_init(self._cond, None)

        if res:
            raise OSError(res, os.strerror(res))

    def __del__(self):
        try:
            _libpthread.pthread_cond_destroy(self._cond)
        except AttributeError:
            if _libpthread is not None:
                raise

    def signal(self):
        return _libpthread.pthread_cond_signal(self._cond)

    def broadcast(self):
        return _libpthread.pthread_cond_broadcast(self._cond)

    def wait(self, mutex=None):
        m = mutex if mutex else self._lock
        return _libpthread.pthread_cond_wait(self._cond, m.mutex())

    def timedwait(self, abstime, mutex=None):
        m = mutex if mutex else self._lock
        return _libpthread.pthread_cond_timedwait(self._cond, m.mutex(),
            C.pointer(abstime))
