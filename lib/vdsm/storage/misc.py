#
# Copyright 2009-2017 Red Hat, Inc.
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

# FIXME: A lot of methods here use DD. A smart thing would be to wrap DD in a
# method that does all the arg concatenation and stream handling once. Also
# most method when they fail don't tell why even though DD is more then happy
# to let you know. Exceptions thrown should contain DD's stderr output.

"""
Various storage misc procedures
"""

from __future__ import absolute_import

import errno
import itertools
import logging
import os
import random
import re
import string
import struct
import threading
import uuid
import weakref

from array import array
from functools import wraps, partial

from six.moves import map
from six.moves import queue

from vdsm import commands
from vdsm import constants
from vdsm.common import concurrent
from vdsm.common import logutils
from vdsm.common import proc

from vdsm.storage import exception as se
from vdsm.storage.constants import SECTOR_SIZE

IOUSER = "vdsm"
DIRECTFLAG = "direct"
STR_UUID_SIZE = 36
UUID_HYPHENS = [8, 13, 18, 23]
MEGA = 1 << 20
UNLIMITED_THREADS = -1

log = logging.getLogger('storage.Misc')


def namedtuple2dict(nt):
    return dict(map(lambda f: (f, getattr(nt, f)), nt._fields))


execCmdLogger = logging.getLogger('storage.Misc.excCmd')


execCmd = partial(commands.execCmd, execCmdLogger=execCmdLogger)


def pidExists(pid):
    try:
        os.stat(os.path.join('/proc', str(pid)))
    except OSError as e:
        # The actual exception for 'File does not exists' is ENOENT
        if e.errno == errno.ENOENT:
            return False
        else:
            log.error("Error on stat pid %s (%s)", pid, str(e))

    return True


def readblock(name, offset, size):
    '''
    Read (direct IO) the content of device 'name' at offset, size bytes
    '''

    # direct io must be aligned on block size boundaries
    if (size % 512) or (offset % 512):
        raise se.MiscBlockReadException(name, offset, size)

    left = size
    ret = ""
    baseoffset = offset

    while left > 0:
        (iounit, count, iooffset) = _alignData(left, offset)

        cmd = [constants.EXT_DD, "iflag=%s" % DIRECTFLAG, "skip=%d" % iooffset,
               "bs=%d" % iounit, "if=%s" % name, "count=%s" % count]

        (rc, out, err) = execCmd(cmd, raw=True)
        if rc:
            raise se.MiscBlockReadException(name, offset, size)
        if not validateDDBytes(err.splitlines(), iounit * count):
            raise se.MiscBlockReadIncomplete(name, offset, size)

        ret += out
        left = left % iounit
        offset = baseoffset + size - left
    return ret.splitlines()


def validateDDBytes(ddstderr, size):
    log.debug("err: %s, size: %s" % (ddstderr, size))
    try:
        size = int(size)
    except (ValueError, ):
        raise se.InvalidParameterException("size", str(size))

    if len(ddstderr) != 3:
        raise se.InvalidParameterException("len(ddstderr)", ddstderr)

    try:
        xferred = int(ddstderr[2].split()[0])
    except (ValueError, ):
        raise se.InvalidParameterException("ddstderr", ddstderr[2])

    if xferred != size:
        return False
    return True


def _alignData(length, offset):
    iounit = MEGA
    count = length
    iooffset = offset

    # Keep small IOps in single shot if possible
    if (length < MEGA) and (offset % length == 0) and (length % 512 == 0):
        # IO can be direct + single shot
        count = 1
        iounit = length
        iooffset = offset / iounit
        return (iounit, count, iooffset)

    # Compute largest chunk possible up to 1M for IO
    while iounit > 1:
        if (length >= iounit) and (offset % iounit == 0):
            count = length / iounit
            iooffset = offset / iounit
            break
        iounit = iounit >> 1

    return (iounit, count, iooffset)


def randomStr(strLen):
    return "".join(random.sample(string.letters, strLen))


def parseBool(var):
    if isinstance(var, bool):
        return var
    # Transform: str -> bool
    if var.lower() == 'true':
        return True
    else:
        return False


def checksum(string, numBytes):
    bits = 8 * numBytes
    tmpArray = array('B')
    tmpArray.fromstring(string)
    csum = sum(tmpArray)
    return csum - (csum >> bits << bits)


def packUuid(s):
    # pylint: disable=no-member
    # https://github.com/PyCQA/pylint/issues/961
    value = uuid.UUID(s).int
    high = value // (2 ** 64)
    low = value % (2 ** 64)
    # pack as 128bit little-endian <QQ
    return struct.pack('<QQ', low, high)


def unpackUuid(s):
    low, high = struct.unpack('<QQ', s)
    value = low + high * (2 ** 64)
    return str(uuid.UUID(int=value))


UUID_REGEX = re.compile("^[a-f0-9]{8}-(?:[a-f0-9]{4}-){3}[a-f0-9]{12}$")
UUID_BLANK = "00000000-0000-0000-0000-000000000000"


def validateUUID(uuid, name="uuid", blank=True):
    """
    Ensure that uuid structure is 32 bytes long and is of the form: 8-4-4-4-12
    (where each number depicts the amount of hex digits)

    Even though UUIDs can contain capital letters (because HEX strings are case
    insensitive) we usually compare uuids with the `==` operator, having uuids
    with upper case letters will cause unexpected bug so we filter them out.
    The blank argument specifies if it's allowed for the uuid to be blank or
    not.
    """
    try:
        m = UUID_REGEX.match(uuid)
    except TypeError:
        raise se.InvalidParameterException(name, uuid)

    if m is None:
        raise se.InvalidParameterException(name, uuid)

    if not blank and uuid == UUID_BLANK:
        raise se.InvalidParameterException(name, uuid)


# FIXME: Consider using confutils validator?
def validateInt(number, name):
    try:
        return int(number)
    except:
        raise se.InvalidParameterException(name, number)


def validateN(number, name):
    n = validateInt(number, name)
    if n < 0:
        raise se.InvalidParameterException(name, number)
    return n


def validateSize(size, name):
    """
    Validate number of bytes as string and convert to number of sectors,
    rounding up to next sectors.

    Raises InvalidParameterException if value is not a string or if it could
    not be converted to integer.
    """
    if not isinstance(size, basestring):
        log.error("Number of sectors as int is not supported, use size in "
                  "bytes as string")
        raise se.InvalidParameterException("size", size)
    size = validateN(size, name)
    return (size + SECTOR_SIZE - 1) / SECTOR_SIZE


def parseHumanReadableSize(size):
    # FIXME : Maybe use a regex -> ^(?P<num>\d+)(?P<sizeChar>[KkMmGgTt])$
    # FIXME : Why not support B and be done with it?
    if size.isdigit():
        # No suffix - pass it as is
        return int(size)

    size = size.upper()

    if size.endswith("T"):
        if size[:-1].isdigit():
            return int(size[:-1]) << 40

    if size.endswith("G"):
        if size[:-1].isdigit():
            return int(size[:-1]) << 30

    if size.endswith("M"):
        if size[:-1].isdigit():
            return int(size[:-1]) << 20

    if size.endswith("K"):
        if size[:-1].isdigit():
            return int(size[:-1]) << 10

    # Failing all the above we'd better just return 0
    return 0


class DynamicBarrier(object):
    def __init__(self):
        self._cond = threading.Condition()
        self._busy = False

    def enter(self):
        """
        Enter the dynamic barrier. Returns True if you should be
        the one performing the operation. False if someone already
        did that for you.

        You only have to exit() if you actually entered.

        Example:

        >> if dynamicBarrier.enter():
        >>    print "Do stuff"
        >>    dynamicBarrier.exit()
        """
        with self._cond:
            if not self._busy:
                # The first thread entered the barrier.
                self._busy = True
                return True

            self._cond.wait()

            # The first thread has exited. Threads waiting here do not know
            # when the barrier was entered, and so they cannot use the result
            # obtained by this thread.

            if not self._busy:
                # The second thread entered the barrier.
                self._busy = True
                return True

            self._cond.wait()

            # The seocnd thread has exited the barrier. Threads waiting here
            # know that the barrier was entered after they tried to enter the
            # barrier, so they can safely use the result obtained by the second
            # thread.

            return False

    def exit(self):
        with self._cond:
            if not self._busy:
                raise AssertionError("Attempt to exit a barrier without "
                                     "entering")
            self._busy = False
            self._cond.notifyAll()


class SamplingMethod(object):
    """
    This class is meant to be used as a decorator. Concurrent calls to the
    decorated function will be evaluated only once, and will share the same
    result, regardless of their specific arguments. It is the responsibility of
    the user of this decorator to make sure that this behavior is the expected
    one.

    Don't use this decorator on recursive functions!

    In addition, if an exception is thrown, only the function running it will
    get the exception, the rest will get previous run results.

    Supporting parameters or exception passing to all functions would
    make the code much more complex for no reason.
    """
    _log = logging.getLogger("storage.SamplingMethod")

    def __init__(self, func):
        self.__func = func
        self.__lastResult = None
        self.__barrier = DynamicBarrier()

        if hasattr(self.__func, "func_name"):
            self.__funcName = self.__func.__name__
        else:
            self.__funcName = str(self.__func)

        self.__funcParent = None

    def __call__(self, *args, **kwargs):
        if self.__funcParent is None:
            if (hasattr(self.__func, "func_code") and
                    self.__func.__code__.co_varnames == 'self'):
                self.__funcParent = args[0].__class__.__name__
            else:
                self.__funcParent = self.__func.__module__

        self._log.debug("Trying to enter sampling method (%s.%s)",
                        self.__funcParent, self.__funcName)
        if self.__barrier.enter():
            try:
                self._log.debug("Got in to sampling method")
                self.__lastResult = self.__func(*args, **kwargs)
            finally:
                self.__barrier.exit()
        else:
            self._log.debug("Some one got in for me")

        self._log.debug("Returning last result")
        return self.__lastResult


def samplingmethod(func):
    sm = SamplingMethod(func)

    @wraps(func)
    def helper(*args, **kwargs):
        return sm(*args, **kwargs)
    return helper


def getfds():
    return [int(fd) for fd in os.listdir("/proc/self/fd")]


class Event(object):

    _count = itertools.count()

    def __init__(self, name, sync=False):
        self._log = logging.getLogger("storage.Event.%s" % name)
        self.name = name
        self._syncRoot = threading.Lock()
        self._registrar = {}
        self._sync = sync

    def register(self, func, oneshot=False):
        with self._syncRoot:
            self._registrar[id(func)] = (weakref.ref(func), oneshot)

    def unregister(self, func):
        with self._syncRoot:
            del self._registrar[id(func)]

    def _emit(self, *args, **kwargs):
        self._log.debug("Emitting event")
        with self._syncRoot:
            for funcId, (funcRef, oneshot) in self._registrar.items():
                func = funcRef()
                if func is None or oneshot:
                    del self._registrar[funcId]
                    if func is None:
                        continue
                try:
                    self._log.debug("Calling registered method `%s`",
                                    logutils.funcName(func))
                    if self._sync:
                        func(*args, **kwargs)
                    else:
                        self._start_thread(func, args, kwargs)
                except:
                    self._log.warn("Could not run registered method because "
                                   "of an exception", exc_info=True)

        self._log.debug("Event emitted")

    def emit(self, *args, **kwargs):
        if len(self._registrar) > 0:
            self._start_thread(self._emit, args, kwargs)

    def _start_thread(self, func, args, kwargs):
        name = "event/%d" % next(self._count)
        t = concurrent.thread(func, args=args, kwargs=kwargs, name=name)
        t.start()


# Sentinel for checking if an error was caught. Using this instead of None
# helps pylint to analyze the code.
_NO_ERROR = Exception("No error")


def killall(name, signum, group=False):
    exception = _NO_ERROR
    knownPgs = set()
    pidList = proc.pgrep(name)
    if len(pidList) == 0:
        raise OSError(errno.ESRCH,
                      "Could not find processes named `%s`" % name)

    for pid in pidList:
        try:
            if group:
                pgid = os.getpgid(pid)
                if pgid in knownPgs:
                    # Signal already sent, ignore
                    continue
                knownPgs.add(pgid)

                os.killpg(pgid, signum)
            else:
                os.kill(pid, signum)
        except OSError as e:
            if e.errno == errno.ESRCH:
                # process died in the interim, ignore
                continue
            exception = e

    if exception is not _NO_ERROR:
        raise exception


def itmap(func, iterable, maxthreads=UNLIMITED_THREADS):
    """
    Make an iterator that computes the function using
    arguments from the iterable. It works similar to tmap
    by running each operation in a different thread, this
    causes the results not to return in any particular
    order so it's good if you don't care about the order
    of the results.
    maxthreads stands for maximum threads that we can initiate simultaneosly.
               If we reached to max threads the function waits for thread to
               finish before initiate the next one.
    """
    if maxthreads < 1 and maxthreads != UNLIMITED_THREADS:
        raise ValueError("Wrong input to function itmap: %s", maxthreads)

    respQueue = queue.Queue()

    def wrapper(value):
        try:
            respQueue.put(func(value))
        except Exception as e:
            respQueue.put(e)

    threadsCreated = 0
    threadsCount = 0
    for arg in iterable:
        if maxthreads != UNLIMITED_THREADS:
            if maxthreads == 0:
                # This not supposed to happened. If it does, it's a bug.
                # maxthreads should get to 0 only after threadsCount is
                # greater than 1
                if threadsCount < 1:
                    raise RuntimeError("No thread initiated")
                else:
                    yield respQueue.get()
                    # if yield returns one thread stopped, so we can run
                    # another thread in queue
                    maxthreads += 1
                    threadsCount -= 1

        name = "itmap/%d" % threadsCreated
        t = concurrent.thread(wrapper, args=(arg,), name=name)
        t.start()
        threadsCreated += 1
        threadsCount += 1
        maxthreads -= 1

    # waiting for rest threads to end
    for i in range(threadsCount):
        yield respQueue.get()


def isAscii(s):
    try:
        s.decode('ascii')
        return True
    except (UnicodeDecodeError, UnicodeEncodeError):
        return False


def walk(top, topdown=True, onerror=None, followlinks=False, blacklist=()):
    """Directory tree generator.

    Custom implementation of os.walk that doesn't block if the destination of
    a symlink is on an unreachable blacklisted path (typically a nfs mount).
    All the general os.walk documentation applies.
    """

    # We may not have read permission for top, in which case we can't
    # get a list of the files the directory contains.  os.path.walk
    # always suppressed the exception then, rather than blow up for a
    # minor reason when (say) a thousand readable directories are still
    # left to visit.  That logic is copied here.
    try:
        names = os.listdir(top)
    except OSError as err:
        if onerror is not None:
            onerror(err)
        return

    # Use absolute and normalized blacklist paths
    normblacklist = [os.path.abspath(x) for x in blacklist]

    dirs, nondirs = [], []
    for name in names:
        path = os.path.join(top, name)

        # Begin of the part where we handle the unreachable symlinks
        if os.path.abspath(path) in normblacklist:
            continue

        if not followlinks:
            # Don't use os.path.islink because it relies on the syscall
            # lstat which is getting stuck if the destination is unreachable
            try:
                os.readlink(path)
            except OSError as err:
                # EINVAL is thrown when "path" is not a symlink, in such
                # case continue normally
                if err.errno != errno.EINVAL:
                    raise
                # There is an hidden code path here, if we fail to read the
                # link and the errno is EINVAL then skip the following else
                # code block:
            else:
                nondirs.append(name)
                continue
        # End of the part where we handle the unreachable symlinks

        if os.path.isdir(path):
            dirs.append(name)
        else:
            nondirs.append(name)

    if topdown:
        yield top, dirs, nondirs
    for name in dirs:
        path = os.path.join(top, name)
        if followlinks or not os.path.islink(path):
            for x in walk(path, topdown, onerror, followlinks, blacklist):
                yield x
    if not topdown:
        yield top, dirs, nondirs


def deprecated(f):
    """Used to mark exported methods as deprecated"""
    return f
