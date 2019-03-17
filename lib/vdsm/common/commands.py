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
from __future__ import division

from contextlib import contextmanager
from weakref import proxy
import errno
import io
import logging
import os
import select
import signal
import threading
import time

from vdsm.common import cmdutils
from vdsm.common import constants
from vdsm.common import password
from vdsm.common.cmdutils import command_log_line, retcode_log_line
from vdsm.common.compat import subprocess
from vdsm.common.marks import deprecated
from vdsm.common.osutils import uninterruptible_poll

# Buffsize is 1K because I tested it on some use cases and 1K was fastest. If
# you find this number to be a bottleneck in any way you are welcome to change
# it
BUFFSIZE = 1024


log = logging.getLogger("common.commands")


def run(args, input=None, cwd=None, env=None, sudo=False, setsid=False,
        nice=None, ioclass=None, ioclassdata=None, reset_cpu_affinity=True):
    """
    Starts a command communicate with it, and wait until the command
    terminates. Ensures that the command is killed if an unexpected error is
    raised.

    args are logged when command starts, and are included in the exception if a
    command has failed. If args contain sensitive information that should not
    be logged, such as passwords, they must be wrapped with ProtectedPassword.

    The child process stdout and stderr are always buffered. If you have
    special needs, such as running the command without buffering stdout, or
    create a pipeline of several commands, use the lower level start()
    function.

    Arguments:
        args (list): Command arguments
        input (bytes): Data to send to the command via stdin.
        cwd (str): working directory for the child process
        env (dict): environment of the new child process
        sudo (bool): if set to True, run the command via sudo
        nice (int): if not None, run the command via nice command with the
            specified nice value
        ioclass (int): if not None, run the command with the ionice command
            using specified ioclass value.
        ioclassdata (int): if ioclass is set, the scheduling class data. 0-7
            are valid data (priority levels).
        reset_cpu_affinity (bool): Run the command via the taskset command,
            allowing the child process to run on all cpus (default True).

    Returns:
        The command output (bytes)

    Raises:
        OSError if the command could not start.
        cmdutils.Error if the command terminated with a non-zero exit code.
        utils.TerminatingFailure if command could not be terminated.
    """
    p = start(args,
              stdin=subprocess.PIPE if input else None,
              stdout=subprocess.PIPE,
              stderr=subprocess.PIPE,
              cwd=cwd,
              env=env,
              sudo=sudo,
              setsid=setsid,
              nice=nice,
              ioclass=ioclass,
              ioclassdata=ioclassdata,
              reset_cpu_affinity=reset_cpu_affinity)

    with terminating(p):
        out, err = p.communicate(input)

    log.debug(cmdutils.retcode_log_line(p.returncode, err))

    if p.returncode != 0:
        raise cmdutils.Error(args, p.returncode, out, err)

    return out


def start(args, stdin=None, stdout=None, stderr=None, cwd=None, env=None,
          sudo=False, setsid=False, nice=None, ioclass=None, ioclassdata=None,
          reset_cpu_affinity=True):
    """
    Starts a command and return it. The caller is responsible for communicating
    with the commmand, waiting for it, and if needed, terminating it.

    args are always logged when command starts. If args contain sensitive
    information that should not be logged, such as passwords, they must be
    wrapped with ProtectedPassword.

    Arguments:
        args (list): Command arguments
        stdin (file or int): file object or descriptor for sending data to the
            child process stdin.
        stdout (file or int): file object or descriptor for receiving data from
            the child process stdout.
        stderr (file or int): file object or descriptor for receiving data from
            the child process stderr.
        cwd (str): working directory for the child process
        env (dict): environment of the new child process
        sudo (bool): if set to True, run the command via sudo
        nice (int): if not None, run the command via nice command with the
            specified nice value
        ioclass (int): if not None, run the command with the ionice command
            using specified ioclass value.
        ioclassdata (int): if ioclass is set, the scheduling class data. 0-7
            are valid data (priority levels).
        reset_cpu_affinity (bool): Run the command via the taskset command,
            allowing the child process to run on all cpus (default True).

    Returns:
        subprocess.Popen instance or commands.PrivilegedPopen if sudo is True.

    Raises:
        OSError if the command could not start.
    """
    args = cmdutils.wrap_command(
        args,
        with_ioclass=ioclass,
        ioclassdata=ioclassdata,
        with_nice=nice,
        with_setsid=setsid,
        with_sudo=sudo,
        reset_cpu_affinity=reset_cpu_affinity,
    )

    log.debug(cmdutils.command_log_line(args, cwd=cwd))

    args = [password.unprotect(a) for a in args]

    cmd_class = PrivilegedPopen if sudo else subprocess.Popen

    return cmd_class(
        args,
        cwd=cwd,
        stdin=stdin,
        stdout=stdout,
        stderr=stderr,
        env=env)


@deprecated
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

    p = subprocess.Popen(
        command, close_fds=True, cwd=cwd, env=env,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

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


class PrivilegedPopen(subprocess.Popen):
    """
    Subclass of Popen that uses the kill command to send signals to the child
    process.

    The kill(), terminate(), and send_signal() methods will work as expected
    even if the child process is running as root.
    """

    def send_signal(self, sig):
        log.info("Sending signal %d to child process %d", sig, self.pid)
        args = [constants.EXT_KILL, "-%d" % sig, str(self.pid)]
        try:
            run(args, sudo=True, reset_cpu_affinity=False)
        except cmdutils.Error as e:
            log.warning("Error sending signal to child process %d: %s",
                        self.pid, e.err)


@deprecated
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


class TerminatingFailure(Exception):

    msg = "Failed to terminate process {self.pid}: {self.error}"

    def __init__(self, pid, error):
        self.pid = pid
        self.error = error

    def __str__(self):
        return self.msg.format(self=self)


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
