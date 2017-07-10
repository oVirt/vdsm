#
# Copyright 2008-2016 Red Hat, Inc.
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
from fnmatch import fnmatch
from .compat import pickle
import distutils.spawn
import errno
import functools
import glob
import io
import logging
import six
import sys
import os
import random
import select
import shutil
import signal
import socket
import stat
import string
import tempfile
import threading
import time
import weakref

from vdsm.common import zombiereaper

try:
    from ovirt.node.utils.fs import Config
    persist = Config().persist
    unpersist = Config().unpersist
except ImportError:
    persist = lambda name: None
    unpersist = lambda name: None


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


def touchFile(filePath):
    """
    http://www.unix.com/man-page/POSIX/1posix/touch/
    If a file at filePath already exists, its accessed and modified times are
    updated to the current time. Otherwise, the file is created.
    :param filePath: The file to touch
    """
    with open(filePath, 'a'):
        os.utime(filePath, None)


def rmFile(fileToRemove):
    """
    Try to remove a file.

    If the file doesn't exist it's assumed that it was already removed.
    """
    try:
        os.unlink(fileToRemove)
    except OSError as e:
        if e.errno == errno.ENOENT:
            logging.warning("File: %s already removed", fileToRemove)
        else:
            logging.error("Removing file: %s failed", fileToRemove,
                          exc_info=True)
            raise


def rmTree(directoryToRemove):
    """
    Try to remove a directory and all it's contents.

    If the directory doesn't exist it's assumed that it was already removed.
    """
    try:
        shutil.rmtree(directoryToRemove)
    except OSError as e:
        if e.errno == errno.ENOENT:
            logging.warning("Directory: %s already removed", directoryToRemove)
        else:
            raise


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


def forceLink(src, dst):
    """ Makes or replaces a hard link.

    Like os.link() but replaces the link if it exists.
    """
    try:
        os.link(src, dst)
    except OSError as e:
        if e.errno == errno.EEXIST:
            rmFile(dst)
            os.link(src, dst)
        else:
            logging.error("Linking file: %s to %s failed", src, dst,
                          exc_info=True)
            raise


STAT = namedtuple('stat', ('pid', 'comm', 'state', 'ppid', 'pgrp', 'session',
                           'tty_nr', 'tpgid', 'flags', 'minflt', 'cminflt',
                           'majflt', 'cmajflt', 'utime', 'stime', 'cutime',
                           'cstime', 'priority', 'nice', 'num_threads',
                           'itrealvalue', 'starttime', 'vsize', 'rss',
                           'rsslim', 'startcode', 'endcode', 'startstack',
                           'kstkesp', 'kstkeip', 'signal', 'blocked',
                           'sigignore', 'sigcatch', 'wchan', 'nswap',
                           'cnswap', 'exit_signal', 'processor',
                           'rt_priority', 'policy', 'delayacct_blkio_ticks',
                           'guest_time', 'cguest_time'))


def pidStat(pid):
    res = []
    with open("/proc/%d/stat" % pid, "r") as f:
        statline = f.readline()
        procNameStart = statline.find("(")
        procNameEnd = statline.rfind(")")
        res.append(int(statline[:procNameStart]))
        res.append(statline[procNameStart + 1:procNameEnd])
        args = statline[procNameEnd + 2:].split()
        res.append(args[0])
        res.extend([int(item) for item in args[1:]])
        # Only 44 feilds are documented in man page while /proc/pid/stat has 52
        # The rest of the fields contain the process memory layout and
        # exit_code, which are not relevant for our use.
        return STAT._make(res[:len(STAT._fields)])


def iteratePids():
    for path in glob.iglob("/proc/[0-9]*"):
        pid = os.path.basename(path)
        yield int(pid)


def pgrep(name):
    res = []
    for pid in iteratePids():
        try:
            procName = pidStat(pid).comm
            if procName == name:
                res.append(pid)
        except (OSError, IOError):
            continue
    return res


def _parseCmdLine(pid):
    with open("/proc/%d/cmdline" % pid, "rb") as f:
        return tuple(f.read().split("\0")[:-1])


def getCmdArgs(pid):
    res = tuple()
    # Sometimes cmdline is empty even though the process is not a zombie.
    # Retrying seems to solve it.
    while len(res) == 0:
        # cmdline is empty for zombie processes
        if pidStat(pid).state in ("Z", "z"):
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


def NoIntrPoll(pollfun, timeout=-1):
    """
    This wrapper is used to handle the interrupt exceptions that might
    occur during a poll system call. The wrapped function must be defined
    as poll([timeout]) where the special timeout value 0 is used to return
    immediately and -1 is used to wait indefinitely.
    """
    # When the timeout < 0 we shouldn't compute a new timeout after an
    # interruption.
    endtime = None if timeout < 0 else monotonic_time() + timeout

    while True:
        try:
            return pollfun(timeout)
        except (IOError, select.error) as e:
            if e.args[0] != errno.EINTR:
                raise

        if endtime is not None:
            timeout = max(0, endtime - monotonic_time())


class CommandStream(object):
    def __init__(self, command, stdoutcb, stderrcb):
        self._poll = select.epoll()
        self._iocb = {}

        # In case both stderr and stdout are using the same fd the
        # output is squashed to the stdout (given the order of the
        # entries in the dictionary)
        self._iocb[command.stderr.fileno()] = stderrcb
        self._iocb[command.stdout.fileno()] = stdoutcb

        for fd in self._iocb:
            self._poll.register(fd, select.EPOLLIN)

    def _poll_input(self, fileno):
        self._iocb[fileno](os.read(fileno, io.DEFAULT_BUFFER_SIZE))

    def _poll_event(self, fileno):
        self._poll.unregister(fileno)
        del self._iocb[fileno]

    def _poll_timeout(self, timeout):
        fdevents = NoIntrPoll(self._poll.poll, timeout)

        for fileno, event in fdevents:
            if event & select.EPOLLIN:
                self._poll_input(fileno)
            elif event & (select.EPOLLHUP | select.EPOLLERR):
                self._poll_event(fileno)

    @property
    def closed(self):
        return len(self._iocb) == 0

    def receive(self, timeout=None):
        """
        Receiving data from the command can raise OSError
        exceptions as described in read(2).
        """
        if timeout is None:
            poll_remaining = -1
        else:
            endtime = monotonic_time() + timeout

        while not self.closed:
            if timeout is not None:
                poll_remaining = endtime - monotonic_time()
                if poll_remaining <= 0:
                    break

            self._poll_timeout(poll_remaining)

    def close(self):
        self._poll.close()


def stripNewLines(lines):
    return [l[:-1] if l.endswith('\n') else l for l in lines]


def traceback(on="", msg="Unhandled exception"):
    """
    Log a traceback for unhandled execptions.

    :param on: Use specific logger name instead of root logger
    :type on: str
    :param msg: Use specified message for the exception
    :type msg: str
    """
    def decorator(f):
        @functools.wraps(f)
        def wrapper(*a, **kw):
            try:
                return f(*a, **kw)
            except (SystemExit, KeyboardInterrupt) as e:
                log = logging.getLogger(on)
                log.debug("Terminated (%s)", e)
            except:
                log = logging.getLogger(on)
                log.exception(msg)
            raise  # Do not swallow
        return wrapper
    return decorator


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


def tobool(s):
    try:
        if s is None:
            return False
        if type(s) == bool:
            return s
        if s.lower() == 'true':
            return True
        return bool(int(s))
    except:
        return False


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


class memoized(object):
    """
    Decorator that caches a function's return value each time it is called.
    If called later with the same arguments, the cached value is returned, and
    not re-evaluated. There is no support for uncachable arguments.

    Adaptation from http://wiki.python.org/moin/PythonDecoratorLibrary#Memoize
    """
    def __init__(self, func):
        self.func = func
        self.cache = {}
        functools.update_wrapper(self, func)

    def __call__(self, *args):
        try:
            return self.cache[args]
        except KeyError:
            value = self.func(*args)
            self.cache[args] = value
            return value

    def invalidate(self):
        self.cache.clear()

    def __get__(self, obj, objtype):
        """Support instance methods."""
        wrapper = functools.partial(self.__call__, obj)
        wrapper.invalidate = self.cache.clear
        return wrapper


def validateMinimalKeySet(dictionary, reqParams):
    if not all(key in dictionary for key in reqParams):
        raise ValueError


class CommandPath(object):
    def __init__(self, name, *args, **kwargs):
        self.name = name
        self.paths = args
        self._cmd = None
        self._search_path = kwargs.get('search_path', True)

    @property
    def cmd(self):
        if not self._cmd:
            for path in self.paths:
                if os.path.exists(path):
                    self._cmd = path
                    break
            else:
                if self._search_path:
                    self._cmd = distutils.spawn.find_executable(self.name)
                if self._cmd is None:
                    raise OSError(os.errno.ENOENT,
                                  os.strerror(os.errno.ENOENT) + ': ' +
                                  self.name)
        return self._cmd

    def __repr__(self):
        return str(self.cmd)

    def __str__(self):
        return str(self.cmd)

    def __unicode__(self):
        return unicode(self.cmd)


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

            if (timeout > 0) and ((monotonic_time() - startTime) > timeout):
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


@memoized
def isOvirtNode():
    return (os.path.exists('/etc/rhev-hypervisor-release') or
            bool(glob.glob('/etc/ovirt-node-*-release')))


def anyFnmatch(name, patterns):
    """Returns True if any element in the patterns iterable fnmatches name."""
    return any(fnmatch(name, pattern) for pattern in patterns)


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


@contextmanager
def terminating(proc):
    try:
        yield proc
    finally:
        try:
            if proc.poll() is None:
                logging.debug('Terminating process pid=%d' % proc.pid)
                proc.kill()
                proc.wait()
        except Exception as e:
            raise TerminatingFailure(proc.pid, e)


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


def monotonic_time():
    """
    Return the amount of time, in secs, elapsed since a fixed
    arbitrary point in time in the past.
    This function is useful if the client just
    needs to use the difference between two given time points.

    With respect to time.time():
    * The resolution of this function is lower. On Linux,
      the resolution is 1/_SC_CLK_TCK, which in turn depends on
      the value of HZ configured in the kernel. A commonly
      found resolution is 10 (ten) ms.
    * This function is resilient with respect to system clock
      adjustments.
    """
    return os.times()[4]


def random_iface_name(prefix='', max_length=15, digit_only=False):
    """
    Create a network device name with the supplied prefix and a pseudo-random
    suffix, e.g. dummy_ilXaYiSn7. The name is bound to IFNAMSIZ of 16-1 chars.
    """
    suffix_len = max_length - len(prefix)
    suffix_chars = string.digits
    if not digit_only:
        suffix_chars += string.ascii_letters
    suffix = ''.join(random.choice(suffix_chars)
                     for _ in range(suffix_len))
    return prefix + suffix


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
def stopwatch(message, log=logging.getLogger('vds.stopwatch')):
    if log.isEnabledFor(logging.DEBUG):
        start = monotonic_time()
        yield
        elapsed = monotonic_time() - start
        log.debug("%s: %.2f seconds", message, elapsed)
    else:
        yield


def kill_and_rm_pid(pid, pid_file):
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError as e:
        if e.errno == os.errno.ESRCH:  # Already exited
            pass
        else:
            raise
    if pid_file is not None:
        rmFile(pid_file)


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


def rget(dict, keys, default=None):
    """Recursive dictionary.get()

    >>> rget({'a': {'b': 'hello'}}, ('a', 'b'))
    'hello'
    """
    if dict is None:
        return default
    elif len(keys) == 0:
        return dict
    return rget(dict.get(keys[0]), keys[1:], default)


@contextmanager
def atomic_file_write(filename, flag):
    """
    Atomically write into a file.

    Usage:

        with atomic_write('foo.txt', 'w') as f:
            f.write('shrubbery')
            # there are no changes on foo.txt yet
        # now it is changed
    """
    fd, tmp_filename = tempfile.mkstemp(
        dir=os.path.dirname(os.path.abspath(filename)),
        prefix=os.path.basename(filename) + '.',
        suffix='.tmp')
    os.close(fd)
    try:
        if os.path.exists(filename):
            shutil.copyfile(filename, tmp_filename)
        with open(tmp_filename, flag) as f:
            yield f
    except:
        rmFile(tmp_filename)
        raise
    else:
        os.rename(tmp_filename, filename)
