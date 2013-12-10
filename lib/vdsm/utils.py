#
# Copyright 2008-2013 Red Hat, Inc.
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
A module containing miscellaneous functions and classes that are user
plentifuly around vdsm.

.. attribute:: utils.symbolerror

    Contains a reverse dictionary pointing from error string to its error code.
"""
from collections import namedtuple, deque
from fnmatch import fnmatch
from SimpleXMLRPCServer import SimpleXMLRPCServer
from SimpleXMLRPCServer import SimpleXMLRPCRequestHandler
from StringIO import StringIO
from weakref import proxy
import SocketServer
import errno
import fcntl
import functools
import glob
import io
import itertools
import logging
import sys
import os
import pwd
import select
import shutil
import signal
import socket
import stat
import subprocess
import threading
import time
import zombiereaper

from cpopen import CPopen as BetterPopen
from . import constants

# Buffsize is 1K because I tested it on some use cases and 1k was fastets. If
# you find this number to be a bottleneck in any way you are welcome to change
# it
BUFFSIZE = 1024

SUDO_NON_INTERACTIVE_FLAG = "-n"

_THP_STATE_PATH = '/sys/kernel/mm/transparent_hugepage/enabled'
if not os.path.exists(_THP_STATE_PATH):
    _THP_STATE_PATH = '/sys/kernel/mm/redhat_transparent_hugepage/enabled'


class IOCLASS:
    REALTIME = 1
    BEST_EFFORT = 2
    IDLE = 3


class NICENESS:
    NORMAL = 0
    HIGH = 19


class GeneralException(Exception):
    code = 100
    message = "General Exception"

    def __init__(self, *value):
        self.value = value

    def __str__(self):
        return "%s: %s" % (self.message, repr(self.value))

    def response(self):
        return {'status': {'code': self.code, 'message': str(self)}}


class ActionStopped(GeneralException):
    code = 443
    message = "Action was stopped"


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

    If the file doesn't exist is assumed that it was already removed.
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

    If the directory doesn't exist is assumed that it was already removed.
    """
    try:
        shutil.rmtree(directoryToRemove)
    except OSError as e:
        if e.errno == errno.ENOENT:
            logging.warning("Directory: %s already removed", directoryToRemove)
        else:
            raise

IPXMLRPCRequestHandler = SimpleXMLRPCRequestHandler


class IPXMLRPCServer(SimpleXMLRPCServer):
    def __init__(self, addr, requestHandler=IPXMLRPCRequestHandler,
                 logRequests=True, allow_none=False, encoding=None,
                 bind_and_activate=True):
        self.address_family = socket.getaddrinfo(*addr)[0][0]
        SimpleXMLRPCServer.__init__(self, addr, requestHandler,
                                    logRequests, allow_none, encoding,
                                    bind_and_activate)


#Threaded version of SimpleXMLRPCServer
class SimpleThreadedXMLRPCServer(SocketServer.ThreadingMixIn,
                                 IPXMLRPCServer):
    allow_reuse_address = True


def readMemInfo():
    """
    Parse ``/proc/meminfo`` and return its content as a dictionary.

    For a reason unknown to me, ``/proc/meminfo`` is is sometime
    empty when opened. If that happens, the function retries to open it
    3 times.

    :returns: a dictionary representation of ``/proc/meminfo``
    """
    # FIXME the root cause for these retries should be found and fixed
    tries = 3
    meminfo = {}
    while True:
        tries -= 1
        try:
            lines = []
            lines = file('/proc/meminfo').readlines()
            for line in lines:
                var, val = line.split()[0:2]
                meminfo[var[:-1]] = int(val)
            return meminfo
        except:
            logging.warning(lines, exc_info=True)
            if tries <= 0:
                raise
            time.sleep(0.1)


def grepCmd(pattern, paths):
    cmd = [constants.EXT_GREP, '-E', '-H', pattern]
    cmd.extend(paths)
    rc, out, err = execCmd(cmd)
    if rc == 0:
        matches = out  # A list of matching lines
    elif rc == 1:
        matches = []  # pattern not found
    else:
        raise ValueError("rc: %s, out: %s, err: %s" % (rc, out, err))
    return matches


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


def pidStat(pid):
    res = []
    fields = ('pid', 'comm', 'state', 'ppid', 'pgrp', 'session',
              'tty_nr', 'tpgid', 'flags', 'minflt', 'cminflt',
              'majflt', 'cmajflt', 'utime', 'stime', 'cutime',
              'cstime', 'priority', 'nice', 'num_threads',
              'itrealvalue', 'starttime', 'vsize', 'rss', 'rsslim',
              'startcode', 'endcode', 'startstack', 'kstkesp',
              'kstkeip', 'signal', 'blocked', 'sigignore', 'sigcatch',
              'wchan', 'nswap', 'cnswap', 'exit_signal', 'processor',
              'rt_priority', 'policy', 'delayacct_blkio_ticks',
              'guest_time', 'cguest_time')
    stat = namedtuple('stat', fields)
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
        return stat._make(res[:len(fields)])


def convertToStr(val):
    varType = type(val)
    if varType is float:
        return '%.2f' % (val)
    elif varType is int:
        return '%d' % (val)
    else:
        return val


# NOTE: it would be best to try and unify NoIntrCall and NoIntrPoll.
# We could do so defining a new object that can be used as a placeholer
# for the changing timeout value in the *args/**kwargs. This would
# lead us to rebuilding the function arguments at each loop.
def NoIntrPoll(pollfun, timeout=-1):
    """
    This wrapper is used to handle the interrupt exceptions that might
    occur during a poll system call. The wrapped function must be defined
    as poll([timeout]) where the special timeout value 0 is used to return
    immediately and -1 is used to wait indefinitely.
    """
    # When the timeout < 0 we shouldn't compute a new timeout after an
    # interruption.
    endtime = None if timeout < 0 else time.time() + timeout

    while True:
        try:
            return pollfun(timeout)
        except (IOError, select.error) as e:
            if e.args[0] != errno.EINTR:
                raise

        if endtime is not None:
            timeout = max(0, endtime - time.time())


class AsyncProc(object):
    """
    AsyncProc is a funky class. It warps a standard subprocess.Popen
    Object and gives it super powers. Like the power to read from a stream
    without the fear of deadlock. It does this by always sampling all
    stream while waiting for data. By doing this the other process can freely
    write data to all stream without the fear of it getting stuck writing
    to a full pipe.
    """
    class _streamWrapper(io.RawIOBase):
        def __init__(self, parent, streamToWrap, fd):
            io.IOBase.__init__(self)
            self._stream = streamToWrap
            self._parent = proxy(parent)
            self._fd = fd
            self._closed = False

        def close(self):
            if not self._closed:
                self._closed = True
                while not self._streamClosed:
                    self._parent._processStreams()

        @property
        def closed(self):
            return self._closed

        @property
        def _streamClosed(self):
            return (self.fileno() in self._parent._closedfds)

        def fileno(self):
            return self._fd

        def seekable(self):
            return False

        def readable(self):
            return True

        def writable(self):
            return True

        def _readNonBlock(self, length):
            hasNewData = (self._stream.len - self._stream.pos)
            if hasNewData < length and not self._streamClosed:
                self._parent._processStreams()

            with self._parent._streamLock:
                res = self._stream.read(length)
                if self._stream.pos == self._stream.len:
                    self._stream.truncate(0)

            if res == "" and not self._streamClosed:
                return None
            else:
                return res

        def read(self, length):
            if not self._parent.blocking:
                return self._readNonBlock(length)
            else:
                res = None
                while res is None:
                    res = self._readNonBlock(length)

                return res

        def readinto(self, b):
            data = self.read(len(b))
            if data is None:
                return None

            bytesRead = len(data)
            b[:bytesRead] = data

            return bytesRead

        def write(self, data):
            if hasattr(data, "tobytes"):
                data = data.tobytes()
            with self._parent._streamLock:
                oldPos = self._stream.pos
                self._stream.pos = self._stream.len
                self._stream.write(data)
                self._stream.pos = oldPos

            while self._stream.len > 0 and not self._streamClosed:
                self._parent._processStreams()

            if self._streamClosed:
                self._closed = True

            if self._stream.len != 0:
                raise IOError(errno.EPIPE,
                              "Could not write all data to stream")

            return len(data)

    def __init__(self, popenToWrap):
        self._streamLock = threading.Lock()
        self._proc = popenToWrap

        self._stdout = StringIO()
        self._stderr = StringIO()
        self._stdin = StringIO()

        fdout = self._proc.stdout.fileno()
        fderr = self._proc.stderr.fileno()
        self._fdin = self._proc.stdin.fileno()

        self._closedfds = []

        self._poller = select.epoll()
        self._poller.register(fdout, select.EPOLLIN | select.EPOLLPRI)
        self._poller.register(fderr, select.EPOLLIN | select.EPOLLPRI)
        self._poller.register(self._fdin, 0)
        self._fdMap = {fdout: self._stdout,
                       fderr: self._stderr,
                       self._fdin: self._stdin}

        self.stdout = io.BufferedReader(self._streamWrapper(self,
                                        self._stdout, fdout), BUFFSIZE)

        self.stderr = io.BufferedReader(self._streamWrapper(self,
                                        self._stderr, fderr), BUFFSIZE)

        self.stdin = io.BufferedWriter(self._streamWrapper(self,
                                       self._stdin, self._fdin), BUFFSIZE)

        self._returncode = None

        self.blocking = False

    def _processStreams(self):
        if len(self._closedfds) == 3:
            return

        if not self._streamLock.acquire(False):
            self._streamLock.acquire()
            self._streamLock.release()
            return
        try:
            if self._stdin.len > 0 and self._stdin.pos == 0:
                # Polling stdin is redundant if there is nothing to write
                # trun on only if data is waiting to be pushed
                self._poller.modify(self._fdin, select.EPOLLOUT)

            pollres = NoIntrPoll(self._poller.poll, 1)

            for fd, event in pollres:
                stream = self._fdMap[fd]
                if event & select.EPOLLOUT and self._stdin.len > 0:
                    buff = self._stdin.read(BUFFSIZE)
                    written = os.write(fd, buff)
                    stream.pos -= len(buff) - written
                    if stream.pos == stream.len:
                        stream.truncate(0)
                        self._poller.modify(fd, 0)

                elif event & (select.EPOLLIN | select.EPOLLPRI):
                    data = os.read(fd, BUFFSIZE)
                    oldpos = stream.pos
                    stream.pos = stream.len
                    stream.write(data)
                    stream.pos = oldpos

                elif event & (select.EPOLLHUP | select.EPOLLERR):
                    self._poller.unregister(fd)
                    self._closedfds.append(fd)
                    # I don't close the fd because the original Popen
                    # will do it.

            if self.stdin.closed and self._fdin not in self._closedfds:
                self._poller.unregister(self._fdin)
                self._closedfds.append(self._fdin)
                self._proc.stdin.close()

        finally:
            self._streamLock.release()

    @property
    def pid(self):
        return self._proc.pid

    @property
    def returncode(self):
        if self._returncode is None:
            self._returncode = self._proc.poll()
        return self._returncode

    def kill(self):
        try:
            self._proc.kill()
        except OSError as ex:
            if ex.errno != errno.EPERM:
                raise
            execCmd([constants.EXT_KILL, "-%d" % (signal.SIGTERM,),
                    str(self.pid)], sudo=True)

    def wait(self, timeout=None, cond=None):
        startTime = time.time()
        while self.returncode is None:
            if timeout is not None and (time.time() - startTime) > timeout:
                return False
            if cond is not None and cond():
                return False
            self._processStreams()
        return True

    def communicate(self, data=None):
        if data is not None:
            self.stdin.write(data)
            self.stdin.flush()
        self.stdin.close()

        self.wait()
        return "".join(self.stdout), "".join(self.stderr)

    def __del__(self):
        self._poller.close()


def execCmd(command, sudo=False, cwd=None, data=None, raw=False, logErr=True,
            printable=None, env=None, sync=True, nice=None, ioclass=None,
            ioclassdata=None, setsid=False, execCmdLogger=logging.root,
            deathSignal=0):
    """
    Executes an external command, optionally via sudo.

    IMPORTANT NOTE: don't define a deathSignal when sync=False
    """
    if ioclass is not None:
        cmd = command
        command = [constants.EXT_IONICE, '-c', str(ioclass)]
        if ioclassdata is not None:
            command.extend(("-n", str(ioclassdata)))

        command = command + cmd

    if nice is not None:
        command = [constants.EXT_NICE, '-n', str(nice)] + command

    if setsid:
        command = [constants.EXT_SETSID] + command

    if sudo:
        command = [constants.EXT_SUDO, SUDO_NON_INTERACTIVE_FLAG] + command

    if not printable:
        printable = command

    cmdline = repr(subprocess.list2cmdline(printable))
    execCmdLogger.debug("%s (cwd %s)", cmdline, cwd)

    p = BetterPopen(command, close_fds=True, cwd=cwd, env=env,
                    deathSignal=deathSignal)
    p = AsyncProc(p)
    if not sync:
        if data is not None:
            p.stdin.write(data)
            p.stdin.flush()

        return p

    (out, err) = p.communicate(data)

    if out is None:
        # Prevent splitlines() from barfing later on
        out = ""

    execCmdLogger.debug("%s: <err> = %s; <rc> = %d",
                        {True: "SUCCESS", False: "FAILED"}[p.returncode == 0],
                        repr(err), p.returncode)

    if not raw:
        out = out.splitlines(False)
        err = err.splitlines(False)

    return (p.returncode, out, err)


def stripNewLines(lines):
    return [l[:-1] if l.endswith('\n') else l for l in lines]


def watchCmd(command, stop, cwd=None, data=None, recoveryCallback=None,
             nice=None, ioclass=None, execCmdLogger=logging.root,
             deathSignal=signal.SIGKILL):
    """
    Executes an external command, optionally via sudo with stop abilities.
    """
    proc = execCmd(command, sudo=False, cwd=cwd, data=data, sync=False,
                   nice=nice, ioclass=ioclass, execCmdLogger=execCmdLogger,
                   deathSignal=deathSignal)
    if recoveryCallback:
        recoveryCallback(proc)

    if not proc.wait(cond=stop):
        proc.kill()
        raise ActionStopped()

    out = stripNewLines(proc.stdout)
    err = stripNewLines(proc.stderr)

    execCmdLogger.debug("%s: <err> = %s; <rc> = %d",
                        {True: "SUCCESS", False: "FAILED"}
                        [proc.returncode == 0],
                        repr(err), proc.returncode)

    return (proc.returncode, out, err)


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
            except Exception:
                log = logging.getLogger(on)
                log.exception(msg)
                raise  # Do not swallow
        return wrapper
    return decorator


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


def _getAllMacs():

    # (
    #     find /sys/class/net/*/device | while read f; do \
    #         cat "$(dirname "$f")/address"; \
    #     done; \
    #     [ -d /proc/net/bonding ] && \
    #         find /proc/net/bonding -type f -exec cat '{}' \; | \
    #         grep 'Permanent HW addr:' | \
    #         sed 's/.* //'
    # ) | sed -e '/00:00:00:00/d' -e '/^$/d'

    macs = []
    for b in glob.glob('/sys/class/net/*/device'):
        mac = file(os.path.join(os.path.dirname(b), "address")). \
            readline().replace("\n", "")
        macs.append(mac)

    for b in glob.glob('/proc/net/bonding/*'):
        for line in file(b):
            if line.startswith("Permanent HW addr: "):
                macs.append(line.split(": ")[1].replace("\n", ""))

    return set(macs) - set(["", "00:00:00:00:00:00"])

__hostUUID = None


def getHostUUID(legacy=True):
    global __hostUUID
    if __hostUUID:
        return __hostUUID

    __hostUUID = None

    try:
        if os.path.exists(constants.P_VDSM_NODE_ID):
            with open(constants.P_VDSM_NODE_ID) as f:
                __hostUUID = f.readline().replace("\n", "")
        else:
            p = subprocess.Popen([constants.EXT_SUDO,
                                  constants.EXT_DMIDECODE, "-s",
                                  "system-uuid"],
                                 close_fds=True, stdin=subprocess.PIPE,
                                 stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE)
            out, err = p.communicate()
            out = '\n'.join(line for line in out.splitlines()
                            if not line.startswith('#'))

            if p.returncode == 0 and 'Not' not in out:
                #Avoid error string - 'Not Settable' or 'Not Present'
                __hostUUID = out.strip()
            else:
                logging.warning('Could not find host UUID.')

            if legacy:
                try:
                    mac = sorted(_getAllMacs())[0]
                except:
                    mac = ""
                    logging.warning('Could not find host MAC.', exc_info=True)

                # __hostUUID might contain the string 'None' returned
                # from dmidecode call
                if __hostUUID and __hostUUID is not 'None':
                    __hostUUID += "_" + mac
                else:
                    __hostUUID = "_" + mac
    except:
        logging.error("Error retrieving host UUID", exc_info=True)

    if legacy and not __hostUUID:
        return 'None'
    return __hostUUID

symbolerror = {}
for code, symbol in errno.errorcode.iteritems():
    symbolerror[os.strerror(code)] = symbol


def getUserPermissions(userName, path):
    """
    Return a dictionary with user specific permissions with respect to the
    given file
    """
    def isRead(bits):
        return (bits & 4) is not 0

    def isWrite(bits):
        return (bits & 2) is not 0

    def isExec(bits):
        return (bits & 1) is not 0

    fileStats = os.stat(path)
    userInfo = pwd.getpwnam(userName)
    permissions = {}
    otherBits = fileStats.st_mode
    groupBits = otherBits >> 3
    ownerBits = groupBits >> 3
    # TODO: Don't ignore user's auxiliary groups
    isSameGroup = userInfo.pw_gid == fileStats.st_gid
    isSameOwner = userInfo.pw_uid == fileStats.st_uid

    # 'Other' permissions are the base permissions
    permissions['read'] = (isRead(otherBits) or
                           isSameGroup and isRead(groupBits) or
                           isSameOwner and isRead(ownerBits))

    permissions['write'] = (isWrite(otherBits) or
                            isSameGroup and isWrite(groupBits) or
                            isSameOwner and isWrite(ownerBits))

    permissions['exec'] = (isExec(otherBits) or
                           isSameGroup and isExec(groupBits) or
                           isSameOwner and isExec(ownerBits))

    return permissions


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


def listJoin(elem, *lists):
    if lists == []:
        return []
    l = list(lists[0])
    for i in lists[1:]:
        l += [elem] + i
    return l


def closeOnExec(fd):
    old = fcntl.fcntl(fd, fcntl.F_GETFD, 0)
    fcntl.fcntl(fd, fcntl.F_SETFD, old | fcntl.FD_CLOEXEC)


class memoized(object):
    """Decorator that caches a function's return value each time it is called.
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

    def __get__(self, obj, objtype):
        """Support instance methods."""
        return functools.partial(self.__call__, obj)


def validateMinimalKeySet(dictionary, reqParams):
    if not all(key in dictionary for key in reqParams):
        raise ValueError


class CommandPath(object):
    def __init__(self, name, *args):
        self.name = name
        self.paths = args
        self._cmd = None

    @property
    def cmd(self):
        if not self._cmd:
            for path in self.paths:
                if os.path.exists(path):
                    self._cmd = path
                    break
            else:
                raise OSError(os.errno.ENOENT,
                              os.strerror(os.errno.ENOENT) + ': ' + self.name)
        return self._cmd

    def __repr__(self):
        return str(self.cmd)

    def __str__(self):
        return str(self.cmd)

    def __unicode__(self):
        return unicode(self.cmd)


class PollEvent(object):
    def __init__(self):
        self._r, self._w = os.pipe()
        self._lock = threading.Lock()
        self._isSet = False

    def fileno(self):
        return self._r

    def set(self):
        with self._lock:
            if self._isSet:
                return

            while True:
                try:
                    os.write(self._w, "a")
                    break
                except (OSError, IOError) as e:
                    if e.errno not in (errno.EINTR, errno.EAGAIN):
                        raise

            self._isSet = True

    def isSet(self):
        return self._isSet

    def clear(self):
        with self._lock:
            if not self._isSet:
                return

            while True:
                try:
                    os.read(self._r, 1)
                    break
                except (OSError, IOError) as e:
                    if e.errno not in (errno.EINTR, errno.EAGAIN):
                        raise
            self._isSet = False

    def __del__(self):
        os.close(self._r)
        os.close(self._w)


def retry(func, expectedException=Exception, tries=None,
          timeout=None, sleep=1, stopCallback=None):
    """
    Retry a function. Wraps the retry logic so you don't have to
    implement it each time you need it.

    :param func: The callable to run.
    :param expectedException: The exception you expect to receive when the
                              function fails.
    :param tries: The number of time to try. None\0,-1 means infinite.
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

    startTime = time.time()

    while True:
        tries -= 1
        try:
            return func()
        except expectedException:
            if tries == 0:
                raise

            if (timeout > 0) and ((time.time() - startTime) > timeout):
                raise

            if stopCallback is not None and stopCallback():
                raise

            time.sleep(sleep)


class AsyncProcessOperation(object):
    def __init__(self, proc, resultParser=None):
        """Wraps a running process operation.

        resultParser should be of type callback(rc, out, err) and can return
        anything or throw exceptions."""
        self._lock = threading.Lock()

        self._result = None
        self._resultParser = resultParser

        self._proc = proc

    def wait(self, timeout=None, cond=None):
        """Waits until the process has exited, the timeout has been reached or
        the condition has been met"""
        return self._proc.wait(timeout, cond)

    def stop(self):
        """Stops the running operation, effectively sending a kill signal to
        the process"""
        self._proc.kill()

    def result(self):
        """Returns the result in the as a tuple of (result, error).
        If the operation is still running it will block until it returns.

        If no resultParser has been set the default result
        is (rc, out, err) """
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


def panic(msg):
    logging.error("Panic: %s", msg, exc_info=True)
    os.killpg(0, 9)
    sys.exit(-3)


# Copied from
# http://docs.python.org/2.6/library/itertools.html?highlight=grouper#recipes
def grouper(iterable, n, fillvalue=None):
    "Collect data into fixed-length chunks or blocks"
    # grouper('ABCDEFG', 3, 'x') --> ABC DEF Gxx
    args = [iter(iterable)] * n
    return itertools.izip_longest(fillvalue=fillvalue, *args)


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
