#
# Copyright 2014 Red Hat, Inc.
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
shared utilities and common code for the virt package
"""

import os.path
import threading

from vdsm.utils import monotonic_time, rmFile


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

    def __nonzero__(self):
        now = self._clock()
        with self._lock:
            expired_keys = [
                key for key, (expiration, _)
                in self._items.iteritems()
                if expiration <= now]
            for key in expired_keys:
                del self._items[key]

            return bool(self._items)

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
    if os.path.islink(sock):
        rmFile(os.path.realpath(sock))
    rmFile(sock)
