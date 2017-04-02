#
# Copyright 2008-2017 Red Hat, Inc.
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

"""
A module containing miscellaneous functions and classes that are used
plentifuly around vdsm.

.. attribute:: utils.symbolerror

    Contains a reverse dictionary pointing from error string to its error code.
"""
from collections import namedtuple, deque, OrderedDict
from contextlib import contextmanager
import errno
import functools
import logging
import six
import sys
import os
import socket
import stat
import threading
import time
import weakref

from vdsm.common import zombiereaper
from vdsm.common import time as vdsm_time
from vdsm.common.compat import pickle
from vdsm.common.proc import pidstat

_THP_STATE_PATH = '/sys/kernel/mm/transparent_hugepage/enabled'
if not os.path.exists(_THP_STATE_PATH):
    _THP_STATE_PATH = '/sys/kernel/mm/redhat_transparent_hugepage/enabled'


class TerminatingFailure(Exception):

    msg = "Failed to terminate process {self.pid}: {self.error}"

    def __init__(self, pid, error):
        self.pid = pid
        self.error = error

    def __str__(self):
        return self.msg.format(self=self)


class IOCLASS:
    REALTIME = 1
    BEST_EFFORT = 2
    IDLE = 3


class NICENESS:
    NORMAL = 0
    HIGH = 19


def isBlockDevice(path):
    path = os.path.abspath(path)
    return stat.S_ISBLK(os.stat(path).st_mode)


def _parseMemInfo(lines):
    """
    Parse the content of ``/proc/meminfo`` as list of strings
    and return its content as a dictionary.
    """
    meminfo = {}
    for line in lines:
        var, val = line.split()[0:2]
        meminfo[var[:-1]] = int(val)
    return meminfo


def readMemInfo():
    """
    Parse ``/proc/meminfo`` and return its content as a dictionary.

    For a reason unknown to me, ``/proc/meminfo`` is sometimes
    empty when opened. If that happens, the function retries to open it
    3 times.

    :returns: a dictionary representation of ``/proc/meminfo``
    """
    # FIXME the root cause for these retries should be found and fixed
    tries = 3
    while True:
        tries -= 1
        try:
            with open('/proc/meminfo') as f:
                lines = f.readlines()
                return _parseMemInfo(lines)
        except:
            logging.warning(lines, exc_info=True)
            if tries <= 0:
                raise
            time.sleep(0.1)


def _parseCmdLine(pid):
    with open("/proc/%d/cmdline" % pid, "rb") as f:
        return tuple(f.read().split("\0")[:-1])


def getCmdArgs(pid):
    res = tuple()
    # Sometimes cmdline is empty even though the process is not a zombie.
    # Retrying seems to solve it.
    while len(res) == 0:
        # cmdline is empty for zombie processes
        if pidstat(pid).state in ("Z", "z"):
            return tuple()

        res = _parseCmdLine(pid)

    return res


def convertToStr(val):
    varType = type(val)
    if varType is float:
        return '%.2f' % (val)
    elif varType is int:
        return '%d' % (val)
    else:
        return val


def stripNewLines(lines):
    return [l[:-1] if l.endswith('\n') else l for l in lines]


class Canceled(BaseException):
    """
    Raised by methods decorated with @cancelpoint.

    Objects using cancellation points may like to handle this exception for
    cleaning up after cancellation.

    Inherits from BaseException so it can propagate through normal Exception
    handlers.
    """


def cancelpoint(meth):
    """
    Decorate a method so it raises Canceled exception if the methods is invoked
    after the object was canceled.

    Decorated object must implement __canceled__ method, returning truthy value
    if the object is canceled.
    """
    @functools.wraps(meth)
    def wrapper(self, *a, **kw):
        if self.__canceled__():
            raise Canceled()
        value = meth(self, *a, **kw)
        if self.__canceled__():
            raise Canceled()
        return value
    return wrapper


symbolerror = {}
for code, symbol in six.iteritems(errno.errorcode):
    symbolerror[os.strerror(code)] = symbol


def listSplit(l, elem, maxSplits=None):
    splits = []
    splitCount = 0

    while True:
        try:
            splitOffset = l.index(elem)
        except ValueError:
            break

        splits.append(l[:splitOffset])
        l = l[splitOffset + 1:]
        splitCount += 1
        if maxSplits is not None and splitCount >= maxSplits:
            break

    return splits + [l]


class closing(object):
    """
    Context Manager that is responsible for closing the object it gets upon
    completion of the with statement.
    __exit__ will be called in the end of the with statement and in case of
    exception during the object lifetime.

    Adaptation from https://docs.python.org/2.7/library/contextlib.html
    """
    def __init__(self, obj, log="utils.closing"):
        self.obj = obj
        self.log = log

    def __enter__(self):
        return self.obj

    def __exit__(self, t, v, tb):
        try:
            self.obj.close()
        except Exception:
            if t is None:
                raise
            log = logging.getLogger(self.log)
            log.exception("Error closing %s", self.obj)


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

    startTime = vdsm_time.monotonic_time()

    while True:
        tries -= 1
        try:
            return func()
        except expectedException:
            if tries == 0:
                raise

            if (timeout > 0) and ((vdsm_time.monotonic_time() - startTime) >
                                  timeout):
                raise

            if stopCallback is not None and stopCallback():
                raise

            time.sleep(sleep)


class AsyncProcessOperation(object):
    def __init__(self, proc, resultParser=None):
        """
        Wraps a running process operation.

        resultParser should be of type callback(rc, out, err) and can return
        anything or throw exceptions.
        """
        self._lock = threading.Lock()

        self._result = None
        self._resultParser = resultParser

        self._proc = proc

    def wait(self, timeout=None, cond=None):
        """
        Waits until the process has exited, the timeout has been reached or
        the condition has been met
        """
        return self._proc.wait(timeout, cond)

    def stop(self):
        """
        Stops the running operation, effectively sending a kill signal to
        the process
        """
        self._proc.kill()

    def result(self):
        """
        Returns the result as a tuple of (result, error).
        If the operation is still running it will block until it returns.

        If no resultParser has been set the default result
        is (rc, out, err)
        """
        with self._lock:
            if self._result is None:
                out, err = self._proc.communicate()
                rc = self._proc.returncode
                if self._resultParser is not None:
                    try:
                        self._result = (self._resultParser(rc, out, err),
                                        None)
                    except Exception as e:
                        self._result = (None, e)
                else:
                    self._result = ((rc, out, err), None)

            return self._result

    def __del__(self):
        if self._proc.returncode is None:
            zombiereaper.autoReapPID(self._proc.pid)


class Callback(namedtuple('Callback_', ('func', 'args', 'kwargs'))):
    log = logging.getLogger("utils.Callback")

    def __call__(self):
        result = None
        try:
            self.log.debug('Calling %s with args=%s and kwargs=%s',
                           self.func.__name__, self.args, self.kwargs)
            result = self.func(*self.args, **self.kwargs)
        except Exception:
            self.log.error("%s failed", self.func.__name__, exc_info=True)
        return result


class CallbackChain(threading.Thread):
    """
    Encapsulates the pattern of calling multiple alternative functions
    to achieve some action.

    The chain ends when the action succeeds (indicated by a callback
    returning True) or when it runs out of alternatives.
    """
    log = logging.getLogger("utils.CallbackChain")

    def __init__(self, callbacks=()):
        """
        :param callbacks:
            iterable of callback objects. Individual callback should be
            callable and when invoked should return True/False based on whether
            it was successful in accomplishing the chain's action.
        """
        super(CallbackChain, self).__init__()
        self.daemon = True
        self.callbacks = deque(callbacks)

    def run(self):
        """Invokes serially the callback objects until any reports success."""
        try:
            self.log.debug("Starting callback chain.")
            while self.callbacks:
                callback = self.callbacks.popleft()
                if callback():
                    self.log.debug("Succeeded after invoking " +
                                   callback.func.__name__)
                    return
            self.log.debug("Ran out of callbacks")
        except Exception:
            self.log.error("Unexpected CallbackChain error", exc_info=True)

    def addCallback(self, func, *args, **kwargs):
        """
        :param func:
            the callback function
        :param args:
            args of the callback
        :param kwargs:
            kwargs of the callback
        :return:
        """
        self.callbacks.append(Callback(func, args, kwargs))


class RollbackContext(object):
    '''
    A context manager for recording and playing rollback.
    The first exception will be remembered and re-raised after rollback

    Sample usage:
    with RollbackContext() as rollback:
        step1()
        rollback.prependDefer(lambda: undo step1)
        def undoStep2(arg): pass
        step2()
        rollback.prependDefer(undoStep2, arg)

    More examples see tests/utilsTests.py
    '''
    def __init__(self, on_exception_only=False):
        self._finally = []
        self._on_exception_only = on_exception_only

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        """
        If this function doesn't return True (or raises a different
        exception), python re-raises the original exception once this
        function is finished.
        """
        if self._on_exception_only and exc_type is None and exc_value is None:
            return

        undoExcInfo = None
        for undo, args, kwargs in self._finally:
            try:
                undo(*args, **kwargs)
            except Exception:
                # keep the earliest exception info
                if undoExcInfo is None:
                    undoExcInfo = sys.exc_info()

        if exc_type is None and undoExcInfo is not None:
            six.reraise(undoExcInfo[0], undoExcInfo[1], undoExcInfo[2])

    def defer(self, func, *args, **kwargs):
        self._finally.append((func, args, kwargs))

    def prependDefer(self, func, *args, **kwargs):
        self._finally.insert(0, (func, args, kwargs))


@contextmanager
def running(runnable):
    runnable.start()
    try:
        yield runnable
    finally:
        runnable.stop()


def terminate(proc):
    try:
        if proc.poll() is None:
            logging.debug('Terminating process pid=%d' % proc.pid)
            proc.kill()
            proc.wait()
    except Exception as e:
        raise TerminatingFailure(proc.pid, e)


@contextmanager
def terminating(proc):
    try:
        yield proc
    finally:
        terminate(proc)


def get_selinux_enforce_mode():
    """
    Returns the SELinux mode as reported by kernel.

    1 = enforcing - SELinux security policy is enforced.
    0 = permissive - SELinux prints warnings instead of enforcing.
    -1 = disabled - No SELinux policy is loaded.
    """
    selinux_mnts = ['/sys/fs/selinux', '/selinux']
    for mnt in selinux_mnts:
        enforce_path = os.path.join(mnt, 'enforce')
        if not os.path.exists(enforce_path):
            continue

        with open(enforce_path) as fileStream:
            return int(fileStream.read().strip())

    # Assume disabled if cannot find
    return -1


def picklecopy(obj):
    """
    Returns a deep copy of argument,
    like copy.deepcopy() does, but faster.

    To be faster, this function leverages the pickle
    module. The following types are safely handled:

    * None, True, and False
    * integers, long integers, floating point numbers,
      complex numbers
    * normal and Unicode strings
    * tuples, lists, sets, and dictionaries containing
      only picklable objects
    * functions defined at the top level of a module
    * built-in functions defined at the top level of a module
    * classes that are defined at the top level of a module
    * instances of such classes whose __dict__ or the
      result of calling __getstate__() is picklable.

    Attempts to pickle unpicklable objects will raise the
    PicklingError exception;
    For full documentation, see:
    https://docs.python.org/2/library/pickle.html
    """
    return pickle.loads(pickle.dumps(obj, pickle.HIGHEST_PROTOCOL))


def round(n, size):
    """
    Round number n to the next multiple of size
    """
    count = int(n + size - 1) // size
    return count * size


def create_connected_socket(host, port, sslctx=None, timeout=None):
    addrinfo = socket.getaddrinfo(host, port,
                                  socket.AF_UNSPEC, socket.SOCK_STREAM)
    family, socktype, proto, _, _ = addrinfo[0]
    sock = socket.socket(family, socktype, proto)

    if sslctx:
        sock = sslctx.wrapSocket(sock)

    sock.settimeout(timeout)
    sock.connect((host, port))
    return sock


@contextmanager
def stopwatch(message, level=logging.DEBUG,
              log=logging.getLogger('vds.stopwatch')):
    if log.isEnabledFor(level):
        start = vdsm_time.monotonic_time()
        yield
        elapsed = vdsm_time.monotonic_time() - start
        log.log(level, "%s: %.2f seconds", message, elapsed)
    else:
        yield


def unique(iterable):
    """
    Return unique items from iterable of hashable objects, keeping the
    original order.
    """
    return OrderedDict.fromkeys(iterable).keys()


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
