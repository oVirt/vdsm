# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

import time
import weakref

from vdsm.common.time import monotonic_time


def retry(func, expectedException=Exception, tries=None,
          timeout=None, sleep=1, stopCallback=None):
    """
    Retry a function. Wraps the retry logic so you don't have to
    implement it each time you need it.

    :param func: The callable to run.
    :param expectedException: The exception you expect to receive when the
                              function fails.
    :param tries: The number of times to try. None\0,-1 means infinite.
    :param timeout: The time you want to spend waiting. This **WILL NOT** stop
                    the method. It will just not run it if it ended after the
                    timeout.
    :param sleep: Time to sleep between calls in seconds.
    :param stopCallback: A function that takes no parameters and causes the
                         method to stop retrying when it returns with a
                         positive value.
    """
    if tries in [0, None]:
        tries = -1

    if timeout in [0, None]:
        timeout = -1

    startTime = monotonic_time()

    while True:
        tries -= 1
        try:
            return func()
        except expectedException:
            if tries == 0:
                raise

            if (timeout > 0) and ((monotonic_time() - startTime) >
                                  timeout):
                raise

            if stopCallback is not None and stopCallback():
                raise

            time.sleep(sleep)


class InvalidatedWeakRef(Exception):
    """
    Stale weakref, the object was deallocated
    """


def weakmethod(meth):
    """
    Return a weakly-referenced wrapper for an instance method.
    Use this function when you want to decorate an instance method
    from the outside, to avoid reference cycles.
    Raise InvalidatedWeakRef if the related instance was collected,
    so the wrapped method is no longer usable.
    """
    func = meth.__func__
    ref = weakref.ref(meth.__self__)

    def wrapper(*args, **kwargs):
        inst = ref()
        if inst is None:
            raise InvalidatedWeakRef()
        return func(inst, *args, **kwargs)

    return wrapper
