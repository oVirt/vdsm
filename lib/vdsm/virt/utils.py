#
# Copyright 2014-2019 Red Hat, Inc.
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

from __future__ import absolute_import
from __future__ import division
import logging
import sys
import time

import six
from six.moves import range

"""
shared utilities and common code for the virt package
"""

import os.path
import threading

from vdsm.common import supervdsm
from vdsm.common.fileutils import rm_file
from vdsm.common.time import monotonic_time


log = logging.getLogger('virt.utils')


def isVdsmImage(drive):
    """
    Tell if drive looks like a vdsm image

    :param drive: drive to check
    :type drive: dict or vm.Drive
    :return: bool
    """
    required = ('domainID', 'imageID', 'poolID', 'volumeID')
    return all(k in drive for k in required)


class ItemExpired(KeyError):
    pass


class ExpiringCache(object):
    """
    ExpiringCache behaves like a dict, but an expiration time
    is attached to each key. Thread safe.

    Parameters:
    ttl: items will expire ttl seconds after they were inserted.
    clock: time.time() or monotonic_time()-like callable.

    Expired keys are removed on the first attempt to access them.
    """
    def __init__(self, ttl, clock=monotonic_time):
        self._ttl = ttl
        self._clock = clock
        self._items = {}
        self._lock = threading.Lock()

    def get(self, key, default=None):
        try:
            return self._get_live(key)
        except KeyError:
            return default

    def clear(self):
        with self._lock:
            self._items.clear()

    def __setitem__(self, key, value):
        item = self._clock() + self._ttl, value
        with self._lock:
            self._items[key] = item

    def __getitem__(self, key):
        """
        If no value is stored for key, raises KeyError.
        If an item expired, raises ItemExpired (a subclass of KeyError).
        """
        return self._get_live(key)

    def __delitem__(self, key):
        with self._lock:
            del self._items[key]

    def __bool__(self):
        now = self._clock()
        with self._lock:
            expired_keys = [
                key for key, (expiration, _)
                in six.iteritems(self._items)
                if expiration <= now]
            for key in expired_keys:
                del self._items[key]

            return bool(self._items)

    # pylint: disable=nonzero-method
    def __nonzero__(self):  # TODO: drop when py2 is no longer needed
        return self.__bool__()

    # private

    def _get_live(self, key):
        now = self._clock()
        with self._lock:
            expiration, value = self._items[key]

            if expiration <= now:
                del self._items[key]
                raise ItemExpired

            return value


def cleanup_guest_socket(sock):
    if sock is None:
        return
    if os.path.islink(sock):
        rm_file(os.path.realpath(sock))
    rm_file(sock)


class DynamicBoundedSemaphore(object):
    """
    Bounded Semaphore with the additional ability
    to dynamically adjust its `bound`.

    The semaphore can be acquired only if the current number of acquisitions
    is strictly smaller than the current `bound` value.

    Specifically when "throttling" the semaphore below the current number
    of concurrent acquisitions, the semaphore will become acquirable again
    only after the semaphore is released by its current users appropriate
    number of times - so the '# of acquisitions' will become smaller than the
    semaphore's new `bound`.
    """

    def __init__(self, value):
        self._cond = threading.Condition(threading.Lock())
        self._value = value
        self._bound = value

    def acquire(self, blocking=True):
        """ Same behavior as threading.BoundedSemaphore.acquire """
        rc = False
        with self._cond:
            # to enable runtime adjustment of semaphore bound
            # we allow the _value counter to reach negative values
            while self._value <= 0:
                if not blocking:
                    break
                self._cond.wait()
            else:
                self._value -= 1
                rc = True
        return rc

    __enter__ = acquire

    def release(self):
        """ Same behavior as threading.BoundedSemaphore.release """
        with self._cond:
            if self._value >= self._bound:
                raise ValueError("Dynamic Semaphore released too many times")
            self._value += 1
            self._cond.notify()

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.release()

    @property
    def bound(self):
        return self._bound

    @bound.setter
    def bound(self, value):
        """ Dynamically updates semaphore bound.

        When the specified value is larger than the previous bound,
        it releases the semaphore the required number of times (and possibly
        wakes that number of waiting threads).

        When the specified value is smaller than the previous bound,
        it simply decreases the current value, which may in doing so become
        negative. Semaphore with value <= 0 is considered unavailable and
        appropriate number of `release()` calls or a new `bound = n` is
        required to make it obtainable again.

        """
        with self._cond:
            delta = value - self._bound
            self._bound = value
            if delta < 0:
                self._value += delta

        # implementation note:
        # if we are increasing the bound we need to do this outside of
        # context manager otherwise release() would deadlock since it also
        # tries to obtain the lock
        if delta > 0:
            for i in range(delta):
                self.release()


def extract_cluster_version(md_values):
    cluster_version = md_values.get('clusterVersion')
    if cluster_version is not None:
        return [int(v) for v in cluster_version.split('.')]
    return None


class TeardownError(Exception):
    pass


class prepared(object):
    """
    A context manager to prepare a group of objects (for example, disk images)
    for an operation.
    """

    def __init__(self, images):
        """
        Receives a variable number of images which must implement
        prepare() and teardown() methods.
        """
        self._images = images
        self._prepared_images = []

    def __enter__(self):
        for image in self._images:
            try:
                image.prepare()
            except:
                exc = sys.exc_info()
                log.error("Error preparing image %r", image)
                try:
                    self._teardown()
                except TeardownError:
                    log.exception("Error tearing down images")
                try:
                    six.reraise(*exc)
                finally:
                    del exc

            self._prepared_images.append(image)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            self._teardown()
        except TeardownError:
            if exc_type is None:
                raise
            # Don't hide the original error
            log.exception("Error tearing down images")

    def _teardown(self):
        errors = []
        while self._prepared_images:
            image = self._prepared_images.pop()
            try:
                image.teardown()
            except Exception as e:
                errors.append(e)
        if errors:
            raise TeardownError(errors)


class LockTimeout(RuntimeError):

    def __init__(self, timeout, lockid, flow):
        self.timeout = timeout
        self.lockid = lockid
        self.flow = flow

    def __str__(self):
        msg = 'waiting more than {elapsed}s for lock {lockid} held by {flow}'
        return msg.format(
            elapsed=self.timeout, lockid=self.lockid, flow=self.flow
        )


class TimedAcquireLock(object):

    def __init__(self, lockid):
        self._lockid = lockid
        self._lock = threading.Lock()
        self._flow = None

    @property
    def holder(self):
        return self._flow

    def acquire(self, timeout, flow=None):
        end = time.time() + timeout
        while not self._lock.acquire(False):
            time.sleep(0.1)
            if time.time() > end:
                raise LockTimeout(timeout, self._lockid, self._flow)

        self._flow = flow

    def release(self):
        self._flow = None
        self._lock.release()


def sasl_enabled():
    """
    Returns true if qemu.conf contains entry for SASL authentication
    for VNC console.
    """
    return supervdsm.getProxy().check_qemu_conf_contains('vnc_sasl', '1')
