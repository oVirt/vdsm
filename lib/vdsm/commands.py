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

from weakref import proxy
import errno
import io
import logging
import os
import select
import signal
import threading
import time

from . import cmdutils
from .utils import terminating
from vdsm.common.cmdutils import command_log_line, retcode_log_line
from vdsm.common.compat import subprocess
from vdsm.common.osutils import uninterruptible_poll
from vdsm import constants

# Buffsize is 1K because I tested it on some use cases and 1K was fastest. If
# you find this number to be a bottleneck in any way you are welcome to change
# it
BUFFSIZE = 1024


def execCmd(command, sudo=False, cwd=None, data=None, raw=False,
            printable=None, env=None, sync=True, nice=None, ioclass=None,
            ioclassdata=None, setsid=False, execCmdLogger=logging.root,
            resetCpuAffinity=True):
    """
    Executes an external command, optionally via sudo.
    """

    command = cmdutils.wrap_command(command, with_ioclass=ioclass,
                                    ioclassdata=ioclassdata, with_nice=nice,
                                    with_setsid=setsid, with_sudo=sudo,
                                    reset_cpu_affinity=resetCpuAffinity)

    # Unsubscriptable objects (e.g. generators) need conversion
    if not callable(getattr(command, '__getitem__', None)):
        command = tuple(command)

    if not printable:
        printable = command

    execCmdLogger.debug(command_log_line(printable, cwd=cwd))

    extra = {}
    extra['stderr'] = subprocess.PIPE
    extra['stdout'] = subprocess.PIPE
    extra['stdin'] = subprocess.PIPE

    p = subprocess.Popen(command, close_fds=True, cwd=cwd, env=env, **extra)

    if not sync:
        p = AsyncProc(p)
        if data is not None:
            p.stdin.write(data)
            p.stdin.flush()

        return p

    with terminating(p):
        (out, err) = p.communicate(data)

    if out is None:
        # Prevent splitlines() from barfing later on
        out = ""

    execCmdLogger.debug(retcode_log_line(p.returncode, err=err))

    if not raw:
        out = out.splitlines(False)
        err = err.splitlines(False)

    return p.returncode, out, err


class AsyncProc(object):
    """
    AsyncProc is a funky class. It wraps a standard subprocess.Popen
    Object and gives it super powers. Like the power to read from a stream
    without the fear of deadlock. It does this by always sampling all
    stream while waiting for data. By doing this the other process can freely
    write data to all stream without the fear of it getting stuck writing
    to a full pipe.
    """
    class _streamWrapper(io.RawIOBase):
        def __init__(self, parent, streamToWrap, fd):
            # pylint: disable=no-member
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
        # this is an ugly hack to let this module load on Python 3, and fail
        # later when AsyncProc is used.
        from StringIO import StringIO

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
                # turn on only if data is waiting to be pushed
                self._poller.modify(self._fdin, select.EPOLLOUT)

            pollres = uninterruptible_poll(self._poller.poll, 1)

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

    def poll(self):
        return self.returncode

    def send_signal(self, signo):
        self._proc.send_signal(signo)

    def terminate(self):
        self._proc.terminate()

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
