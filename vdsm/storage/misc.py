#
# Copyright 2009-2011 Red Hat, Inc.
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
from array import array
from collections import defaultdict
from contextlib import contextmanager
from functools import wraps, partial
from itertools import chain, imap
import contextlib
import errno
import glob
import logging
import os
import Queue
import random
import re
import signal
import select
import string
import struct
import sys
import threading
import types
import weakref
import fcntl
import inspect

from vdsm import constants
from vdsm import utils
import storage_exception as se
import fileUtils
import logUtils
from caps import isOvirtNode

IOUSER = "vdsm"
DIRECTFLAG = "direct"
DATASYNCFLAG = "fdatasync"
STR_UUID_SIZE = 36
UUID_HYPHENS = [8, 13, 18, 23]
MEGA = 1 << 20
UNLIMITED_THREADS = -1

log = logging.getLogger('Storage.Misc')


def namedtuple2dict(nt):
    return dict(imap(lambda f: (f, getattr(nt, f)), nt._fields))


class _LogSkip(object):
    _ignoreMap = defaultdict(list)
    ALL_KEY = "##ALL##"

    @classmethod
    def registerSkip(cls, codeId, loggerName=None):
        if loggerName is None:
            loggerName = cls.ALL_KEY

        cls._ignoreMap[loggerName].append(codeId)

    @classmethod
    def checkForSkip(cls, codeId, loggerName):
        return codeId in chain(cls._ignoreMap[cls.ALL_KEY],
                               cls._ignoreMap[loggerName])

    @classmethod
    def wrap(cls, func, loggerName):
        cls.registerSkip(id(func.func_code), loggerName)
        return func


def logskip(var):
    if isinstance(var, types.StringTypes):
        return lambda func: _LogSkip.wrap(func, var)
    return _LogSkip.wrap(var, None)


@logskip
def enableLogSkip(logger, *args, **kwargs):
    skipFunc = partial(findCaller, *args, **kwargs)
    logger.findCaller = types.MethodType(lambda self: skipFunc(),
                                         logger, logger.__class__)

    return logger


def _shouldLogSkip(skipUp, ignoreSourceFiles, ignoreMethodNames,
                   logSkipName, code, filename):
    if logSkipName is not None:
        if _LogSkip.checkForSkip(id(code), logSkipName):
            return True
    if (skipUp > 0):
        return True
    if (os.path.splitext(filename)[0] in ignoreSourceFiles):
        return True
    if (code.co_name in ignoreMethodNames):
        return True

    return False


def findCaller(skipUp=0, ignoreSourceFiles=(), ignoreMethodNames=(),
               logSkipName=None):
    """
    Find the stack frame of the caller so that we can note the source
    file name, line number and function name.
    """
    # Ignore file extension can be either py or pyc
    ignoreSourceFiles = [os.path.splitext(sf)[0] for sf in
                         chain(ignoreSourceFiles, [logging._srcfile])]
    frame = inspect.currentframe().f_back

    result = "(unknown file)", 0, "(unknown function)"
    # pop frames until you find an unfiltered one
    while hasattr(frame, "f_code"):
        code = frame.f_code
        filename = os.path.normcase(code.co_filename)

        logSkip = _shouldLogSkip(skipUp, ignoreSourceFiles, ignoreMethodNames,
                                 logSkipName, code, filename)

        if logSkip:
            skipUp -= 1
            frame = frame.f_back
            continue

        result = (filename, frame.f_lineno, code.co_name)
        break

    return result


execCmdLogger = enableLogSkip(logging.getLogger('Storage.Misc.excCmd'),
                              ignoreSourceFiles=[__file__],
                              logSkipName="Storage.Misc.excCmd")


execCmd = partial(logskip("Storage.Misc.excCmd")(utils.execCmd),
                  execCmdLogger=execCmdLogger)


watchCmd = partial(utils.watchCmd, execCmdLogger=execCmdLogger)


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


def getProcCtime(pid):
    try:
        stats = os.stat(os.path.join('/proc', str(pid)))
        ctime = stats.st_ctime
    except OSError:
        raise OSError(os.errno.ESRCH,
                      "Could not find process with pid %s" % pid)

    return str(ctime)


def _readfile(name, buffersize=None):
    cmd = [constants.EXT_DD]

    if fileUtils.pathRequiresFlagForDirectIO(name):
        cmd.append("iflag=%s" % DIRECTFLAG)
    cmd.append("if=%s" % name)

    if buffersize:
        cmd.extend(["bs=%d" % buffersize, "count=1"])
    (rc, out, err) = execCmd(cmd, sudo=False)
    if rc:
        raise se.MiscFileReadException(name)

    return rc, out, err


def readfile(name, buffersize=None):
    """
    Read the content of the file using /bin/dd command
    """
    _, out, _ = _readfile(name, buffersize)
    return out


_readspeed_regex = re.compile(
    "(?P<bytes>\d+) bytes? \([\de\-.]+ [kMGT]*B\) copied, "
    "(?P<seconds>[\de\-.]+) s, "
    "[\de\-.]+ [kMGT]*B/s"
)


def readspeed(path, buffersize=None):
    """
    Measures the amount of bytes transferred and the time elapsed
    reading the content of the file/device
    """
    rc, _, err = _readfile(path, buffersize)

    if rc != 0:
        log.error("Unable to read file '%s'", path)
        raise se.MiscFileReadException(path)

    try:
        # The statistics are located in the last line:
        m = _readspeed_regex.match(err[-1])
    except IndexError:
        log.error("Unable to find dd statistics to parse")
        raise se.MiscFileReadException(path)

    if m is None:
        log.error("Unable to parse dd output: '%s'", err[-1])
        raise se.MiscFileReadException(path)

    return {
        'bytes': int(m.group('bytes')),
        'seconds': float(m.group('seconds')),
    }


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

        cmd = [constants.EXT_DD]
        if fileUtils.pathRequiresFlagForDirectIO(name):
            cmd.append("iflag=%s" % DIRECTFLAG)
        cmd.extend(["skip=%d" % iooffset, "bs=%d" % iounit, "if=%s" % name,
                    "count=%s" % count])

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


def ddWatchCopy(src, dst, stop, size, offset=0, recoveryCallback=None):
    """
    Copy src to dst using dd command with stop abilities
    """
    try:
        size = int(size)
    except ValueError:
        raise se.InvalidParameterException("size", "size = %s" % (size,))
    try:
        offset = int(offset)
    except ValueError:
        raise se.InvalidParameterException("offset", "offset = %s" % (offset,))

    left = size
    baseoffset = offset

    while left > 0:
        (iounit, count, iooffset) = _alignData(left, offset)
        oflag = None
        conv = "notrunc"
        if (iounit % 512) == 0:
            if fileUtils.pathRequiresFlagForDirectIO(dst):
                oflag = DIRECTFLAG
        else:
            conv += ",%s" % DATASYNCFLAG

        cmd = [constants.EXT_DD, "if=%s" % src, "of=%s" % dst,
               "bs=%d" % iounit, "seek=%s" % iooffset, "skip=%s" % iooffset,
               "conv=%s" % conv, 'count=%s' % count]

        if oflag:
            cmd.append("oflag=%s" % oflag)

        if not stop:
            (rc, out, err) = execCmd(cmd, sudo=False, nice=utils.NICENESS.HIGH,
                                     ioclass=utils.IOCLASS.IDLE,
                                     deathSignal=signal.SIGKILL)
        else:
            (rc, out, err) = watchCmd(cmd, stop=stop,
                                      recoveryCallback=recoveryCallback,
                                      nice=utils.NICENESS.HIGH,
                                      ioclass=utils.IOCLASS.IDLE)

        if rc:
            raise se.MiscBlockWriteException(dst, offset, size)

        if not validateDDBytes(err, iounit * count):
            raise se.MiscBlockWriteIncomplete(dst, offset, size)

        left = left % iounit
        offset = baseoffset + size - left

    return (rc, out, err)


def ddCopy(src, dst, size):
    """
    Copy src to dst using dd command
    """
    return ddWatchCopy(src, dst, None, size=size)


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
    s = ''.join([c for c in s if c != '-'])
    uuid = int(s, 16)
    high = uuid / 2 ** 64
    low = uuid % 2 ** 64
    # pack as 128bit little-endian <QQ
    return struct.pack('<QQ', low, high)


def unpackUuid(uuid):
    low, high = struct.unpack('<QQ', uuid)
    # remove leading 0x and trailing L
    uuid = hex(low + 2 ** 64 * high)[2:-1].rjust(STR_UUID_SIZE - 4, "0")
    uuid = uuid.lower()
    s = ""
    prev = 0
    i = 0
    for hypInd in UUID_HYPHENS:
        s += uuid[prev:hypInd - i] + '-'
        prev = hypInd - i
        i += 1
    s += uuid[prev:]
    return s


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


#FIXME: Consider using confutils validator?
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


def rotateFiles(directory, prefixName, gen, cp=False, persist=False):
    log.debug("dir: %s, prefixName: %s, versions: %s" %
              (directory, prefixName, gen))
    gen = int(gen)
    files = os.listdir(directory)
    files = glob.glob("%s*" % prefixName)
    fd = {}
    for fname in files:
        name = fname.rsplit('.', 1)
        try:
            ind = int(name[1])
        except ValueError:
            name[0] = fname
            ind = 0
        except IndexError:
            ind = 0
        except:
            continue
        if ind < gen:
            fd[ind] = {'old': fname, 'new': name[0] + '.' + str(ind + 1)}

    keys = fd.keys()
    keys.sort(reverse=True)
    log.debug("versions found: %s" % (keys))

    for key in keys:
        oldName = os.path.join(directory, fd[key]['old'])
        newName = os.path.join(directory, fd[key]['new'])
        if isOvirtNode() and persist and not cp:
            try:
                execCmd([constants.EXT_UNPERSIST, oldName], logErr=False,
                        sudo=True)
                execCmd([constants.EXT_UNPERSIST, newName], logErr=False,
                        sudo=True)
            except:
                pass
        try:
            if cp:
                execCmd([constants.EXT_CP, oldName, newName], sudo=True)
                if isOvirtNode() and persist and not os.path.exists(newName):
                    execCmd([constants.EXT_PERSIST, newName], logErr=False,
                            sudo=True)

            else:
                os.rename(oldName, newName)
        except:
            pass
        if isOvirtNode() and persist and not cp:
            try:
                execCmd([constants.EXT_PERSIST, newName], logErr=False,
                        sudo=True)
            except:
                pass


def persistFile(name):
    if isOvirtNode():
        execCmd([constants.EXT_PERSIST, name], sudo=True)


def parseHumanReadableSize(size):
    #FIXME : Maybe use a regex -> ^(?P<num>\d+)(?P<sizeChar>[KkMmGgTt])$
    #FIXME : Why not support B and be done with it?
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


class RWLock(object):
    """
    A simple ReadWriteLock implementation.

    The lock must be released by the thread that acquired it.  Once a thread
    has acquired a lock, the same thread may acquire it again without blocking;
    the thread must release it once for each time it has acquired it. Note that
    lock promotion (acquiring an exclusive lock under a shared lock is
    forbidden and will raise an exception.

    The lock puts all requests in a queue. The request is granted when The
    previous one is released.

    Each request is represented by a :class:`threading.Event` object. When the
    Event is set the request is granted. This enables multiple callers to wait
    for a request thus implementing a shared lock.
    """
    class _contextLock(object):
        def __init__(self, owner, exclusive):
            self._owner = owner
            self._exclusive = exclusive

        def __enter__(self):
            self._owner.acquire(self._exclusive)

        def __exit__(self, exc_type, exc_value, traceback):
            self._owner.release()

    def __init__(self):
        self._syncRoot = threading.Lock()
        self._queue = Queue.Queue()
        self._currentSharedLock = None
        self._currentState = None
        self._holdingThreads = {}

        self.shared = self._contextLock(self, False)
        self.exclusive = self._contextLock(self, True)

    def acquireRead(self):
        return self.acquire(False)

    def acquireWrite(self):
        return self.acquire(True)

    def acquire(self, exclusive):
        currentEvent = None
        currentThread = threading.currentThread()

        # Handle reacquiring lock in the same thread
        if currentThread in self._holdingThreads:
            if self._currentState is False and exclusive:
                raise RuntimeError("Lock promotion is forbidden.")

            self._holdingThreads[currentThread] += 1
            return

        with self._syncRoot:
            # Handle regular acquisition
            if exclusive:
                currentEvent = threading.Event()
                self._currentSharedLock = None
            else:
                if self._currentSharedLock is None:
                    self._currentSharedLock = threading.Event()

                currentEvent = self._currentSharedLock

            try:
                self._queue.put_nowait((currentEvent, exclusive))
            except Queue.Full:
                raise RuntimeError("There are too many objects waiting for "
                                   "this lock")

            if self._queue.unfinished_tasks == 1:
                # Bootstrap the process if needed. A lock is released the when
                # the next request is granted. When there is no one to grant
                # the request you have to grant it yourself.
                event, self._currentState = self._queue.get_nowait()
                event.set()

        currentEvent.wait()

        self._holdingThreads[currentThread] = 0

    def release(self):
        currentThread = threading.currentThread()

        if not currentThread in self._holdingThreads:
            raise RuntimeError("Releasing an lock without acquiring it first")

        # If in nested lock don't really release
        if self._holdingThreads[currentThread] > 0:
            self._holdingThreads[currentThread] -= 1
            return

        del self._holdingThreads[currentThread]

        with self._syncRoot:
            self._queue.task_done()

            if self._queue.empty():
                self._currentState = None
                return

            nextRequest, self._currentState = self._queue.get_nowait()

        nextRequest.set()


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

    More examples see tests/miscTests.py
    '''
    def __init__(self, *args):
        self._finally = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        firstException = exc_value

        for undo, args, kwargs in self._finally:
            try:
                undo(*args, **kwargs)
            except Exception as e:
                # keep the earliest exception info
                if not firstException:
                    firstException = e
                    # keep the original traceback info
                    traceback = sys.exc_info()[2]

        # re-raise the earliest exception
        if firstException is not None:
            raise firstException, None, traceback

    def defer(self, func, *args, **kwargs):
        self._finally.append((func, args, kwargs))

    def prependDefer(self, func, *args, **kwargs):
        self._finally.insert(0, (func, args, kwargs))


class DynamicBarrier(object):
    def __init__(self):
        self._lock = threading.Lock()
        self._cond = threading.Condition()

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
        self._cond.acquire()
        try:
            if self._lock.acquire(False):
                return True

            self._cond.wait()

            if self._lock.acquire(False):
                return True

            self._cond.wait()
            return False

        finally:
            self._cond.release()

    def exit(self):
        self._cond.acquire()
        try:
            self._lock.release()
            self._cond.notifyAll()
        finally:
            self._cond.release()


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
    _log = logging.getLogger("SamplingMethod")

    def __init__(self, func):
        self.__func = func
        self.__lastResult = None
        self.__barrier = DynamicBarrier()

        if hasattr(self.__func, "func_name"):
            self.__funcName = self.__func.func_name
        else:
            self.__funcName = str(self.__func)

        self.__funcParent = None

    def __call__(self, *args, **kwargs):
        if self.__funcParent is None:
            if (hasattr(self.__func, "func_code") and
                    self.__func.func_code.co_varnames == 'self'):
                self.__funcParent = args[0].__class__.__name__
            else:
                self.__funcParent = self.__func.__module__

        self._log.debug("Trying to enter sampling method (%s.%s)",
                        self.__funcParent, self.__funcName)
        if self.__barrier.enter():
            self._log.debug("Got in to sampling method")
            try:
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


def iteratePids():
    for path in glob.iglob("/proc/[0-9]*"):
        pid = os.path.basename(path)
        yield int(pid)


def pgrep(name):
    res = []
    for pid in iteratePids():
        try:
            pid = int(pid)
        except ValueError:
            continue

        try:
            procName = utils.pidStat(pid).comm
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
        if utils.pidStat(pid).state in ("Z", "z"):
            return tuple()

        res = _parseCmdLine(pid)

    return res


def tmap(func, iterable):
    resultsDict = {}
    error = [None]

    def wrapper(f, arg, index):
        try:
            resultsDict[index] = f(arg)
        except Exception as e:
            # We will throw the last error received
            # we can only throw one error, and the
            # last one is as good as any. This shouldn't
            # happen. Wrapped methods should not throw
            # exceptions, if this happens it's a bug
            log.error("tmap caught an unexpected error", exc_info=True)
            error[0] = e
            resultsDict[index] = None

    threads = []
    for i, arg in enumerate(iterable):
        t = threading.Thread(target=wrapper, args=(func, arg, i))
        threads.append(t)
        t.start()

    for t in threads:
        t.join()

    results = [None] * len(resultsDict)
    for i, result in resultsDict.iteritems():
        results[i] = result

    if error[0] is not None:
        raise error[0]

    return tuple(results)


def getfds():
    return [int(fd) for fd in os.listdir("/proc/self/fd")]


class Event(object):
    def __init__(self, name, sync=False):
        self._log = logging.getLogger("Event.%s" % name)
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
                                    logUtils.funcName(func))
                    if self._sync:
                        func(*args, **kwargs)
                    else:
                        threading.Thread(target=func, args=args,
                                         kwargs=kwargs).start()
                except:
                    self._log.warn("Could not run registered method because "
                                   "of an exception", exc_info=True)

        self._log.debug("Event emitted")

    def emit(self, *args, **kwargs):
        if len(self._registrar) > 0:
            threading.Thread(target=self._emit, args=args,
                             kwargs=kwargs).start()


class OperationMutex(object):
    log = enableLogSkip(logging.getLogger("OperationMutex"),
                        ignoreSourceFiles=[__file__, contextlib.__file__])

    def __init__(self):
        self._lock = threading.Lock()
        self._cond = threading.Condition()
        self._active = None
        self._counter = 0
        self._queueSize = 0

    @contextmanager
    def acquireContext(self, operation):
        self.acquire(operation)
        try:
            yield self
        finally:
            self.release()

    def acquire(self, operation):
        generation = 0
        with self._cond:
            while not self._lock.acquire(False):
                if self._active == operation:
                    if self._queueSize == 0 or generation > 0:
                        self._counter += 1
                        self.log.debug("Got the operational mutex")
                        return

                self._queueSize += 1
                self.log.debug("Operation '%s' is holding the operation mutex,"
                               " waiting...", self._active)
                self._cond.wait()
                generation += 1
                self._queueSize -= 1

            self.log.debug("Operation '%s' got the operation mutex", operation)
            self._active = operation
            self._counter = 1

    def release(self):
        with self._cond:
            self._counter -= 1
            if self._counter == 0:
                self.log.debug("Operation '%s' released the operation mutex",
                               self._active)
                self._lock.release()
                self._cond.notifyAll()


def killall(name, signum, group=False):
    exception = None
    knownPgs = set()
    pidList = pgrep(name)
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

    if exception is not None:
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

    respQueue = Queue.Queue()

    def wrapper(value):
        try:
            respQueue.put(func(value))
        except Exception as e:
            respQueue.put(e)

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

        t = threading.Thread(target=wrapper, args=(arg,))
        t.start()
        threadsCount += 1
        maxthreads -= 1

    # waiting for rest threads to end
    for i in xrange(threadsCount):
        yield respQueue.get()


def NoIntrCall(fun, *args, **kwargs):
    """
    This wrapper is used to handle the interrupt exceptions that might
    occur during a system call.
    """
    while True:
        try:
            return fun(*args, **kwargs)
        except (IOError, select.error) as e:
            if e.args[0] == os.errno.EINTR:
                continue
            raise
        break


NoIntrPoll = utils.NoIntrPoll


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


def setNonBlocking(fd):
    if hasattr(fd, "fileno"):
        fd = fd.fileno()

    fl = fcntl.fcntl(fd, fcntl.F_GETFL)
    fcntl.fcntl(fd, fcntl.F_SETFL, fl | os.O_NONBLOCK)
