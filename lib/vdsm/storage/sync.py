#
# Copyright 2012-2016 Red Hat, Inc.
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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#

from __future__ import absolute_import

from threading import Event
from functools import wraps
from vdsm import concurrent


def AsyncCallStub(result):
    def stubby():
        return result

    return AsyncCall(stubby, [], [])


class AsyncCallNotDone(RuntimeError):
    pass


class AsyncCall(object):
    def __init__(self, f, args, kwargs):
        self._event = Event()
        self._result = None
        self._callable = f
        self._args = args
        self._kwargs = kwargs

    def wait(self, timeout=None):
        self._event.wait(timeout)
        return self._event.isSet()

    def result(self):
        if self._result is None:
            return AsyncCallNotDone()

        return self._result

    def _wrapper(self):
        res = err = None
        try:
            res = self._callable(*self._args, **self._kwargs)
        except Exception as e:
            err = e
        finally:
            self._result = (res, err)
            self._event.set()

    def _call(self):
        t = concurrent.thread(self._wrapper)
        t.start()


def asyncmethod(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        acall = AsyncCall(f, args, kwargs)
        acall._call()

        return acall

    return wrapper
