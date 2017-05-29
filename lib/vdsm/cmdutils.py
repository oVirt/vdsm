#
# Copyright 2014-2017 Red Hat, Inc.
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

import errno
import io
import logging
import os
import select
import time

from . import constants
from . common import errors
from . common import osutils
from . config import config

from . common.time import monotonic_time

SUDO_NON_INTERACTIVE_FLAG = "-n"

# receive() source names
OUT = "out"
ERR = "err"

log = logging.getLogger("procutils")


class Error(errors.Base):
    msg = ("Command {self.cmd} failed with rc={self.rc} out={self.out!r} "
           "err={self.err!r}")

    def __init__(self, cmd, rc, out, err):
        self.cmd = cmd
        self.rc = rc
        self.out = out
        self.err = err


class TimeoutExpired(errors.Base):
    msg = "Timeout waiting for process pid={self.pid}"

    def __init__(self, pid):
        self.pid = pid


def nice(cmd, nice):
    command = [constants.EXT_NICE, '-n', str(nice)]
    command.extend(cmd)
    return command


def ionice(cmd, ioclass, ioclassdata=None):
    command = [constants.EXT_IONICE, '-c', str(ioclass)]
    if ioclassdata is not None:
        command.extend(('-n', str(ioclassdata)))
    command.extend(cmd)
    return command


def setsid(cmd):
    command = [constants.EXT_SETSID]
    command.extend(cmd)
    return command


def sudo(cmd):
    if os.geteuid() == 0:
        return cmd
    command = [constants.EXT_SUDO, SUDO_NON_INTERACTIVE_FLAG]
    command.extend(cmd)
    return command


def taskset(cmd, cpu_list):
    command = [constants.EXT_TASKSET, "--cpu-list", ",".join(cpu_list)]
    command.extend(cmd)
    return command


_ANY_CPU = ["0-%d" % (os.sysconf('SC_NPROCESSORS_CONF') - 1)]
_USING_CPU_AFFINITY = config.get('vars', 'cpu_affinity') != ""


def wrap_command(command, with_ioclass=None, ioclassdata=None,
                 with_nice=None, with_setsid=False, with_sudo=False,
                 reset_cpu_affinity=True):
    if with_ioclass is not None:
        command = ionice(command, ioclass=with_ioclass,
                         ioclassdata=ioclassdata)

    if with_nice is not None:
        command = nice(command, nice=with_nice)

    if with_setsid:
        command = setsid(command)

    if with_sudo:
        command = sudo(command)

    # warning: the order of commands matters. If we add taskset
    # after sudo, we'll need to configure sudoers to allow both
    # 'sudo <command>' and 'sudo taskset <command>', which is
    # impractical. On the other hand, using 'taskset sudo <command>'
    # is much simpler and delivers the same end result.

    if reset_cpu_affinity and _USING_CPU_AFFINITY:
        # only VDSM itself should be bound
        command = taskset(command, _ANY_CPU)

    return command


def receive(p, timeout=None, bufsize=io.DEFAULT_BUFFER_SIZE):
    """
    Receive data from a process, yielding data read from stdout and stderr
    until proccess terminates or timeout expires.

    Unlike Popen.communicate(), this supports a timeout, and allows
    reading both stdout and stderr with a single thread.

    Example usage::

        # Reading data from both stdout and stderr until process
        # terminates:

        for src, data in cmdutils.receive(p):
            if src == cmdutils.OUT:
                # handle output
            elif src == cmdutils.ERR:
                # handler errors

        # Receiving data with a timeout:

        try:
            received = list(cmdutils.receive(p, timeout=10))
        except cmdutils.TimeoutExpired:
            # handle timeout

    Arguments:
        p (`subprocess.Popen`): A subprocess created with
            subprocess.Popen or cpopen.CPopen.
        timeout (float): Number of seconds to wait for process. Timeout
            resolution is limited by the resolution of
            `common.time.monotonic_time`, typically 10 milliseconds.
        bufsize (int): Number of bytes to read from the process in each
            iteration.

    Returns:
        Generator of tuples (SRC, bytes). SRC may be either
        `cmdutils.OUT` or `cmdutils.ERR`, and bytes is a bytes object
        read from process stdout or stderr.

    Raises:
        `cmdutils.TimeoutExpired` if process did not terminate within
            the specified timeout.
    """
    if timeout is not None:
        deadline = monotonic_time() + timeout
        remaining = timeout
    else:
        deadline = None
        remaining = None

    fds = {}
    if p.stdout:
        fds[p.stdout.fileno()] = OUT
    if p.stderr:
        fds[p.stderr.fileno()] = ERR

    if fds:
        poller = select.poll()
        for fd in fds:
            poller.register(fd, select.POLLIN)

        def discard(fd):
            if fd in fds:
                del fds[fd]
                poller.unregister(fd)

    while fds:
        log.debug("Waiting for process (pid=%d, remaining=%s)",
                  p.pid, remaining)
        # Unlike all other time apis, poll is using milliseconds
        remaining_msec = remaining * 1000 if deadline else None
        try:
            ready = poller.poll(remaining_msec)
        except select.error as e:
            if e[0] != errno.EINTR:
                raise
            log.debug("Polling process (pid=%d) interrupted", p.pid)
        else:
            for fd, mode in ready:
                if mode & select.POLLIN:
                    data = osutils.uninterruptible(os.read, fd, bufsize)
                    if not data:
                        log.debug("Fd %d closed, unregistering", fd)
                        discard(fd)
                        continue
                    yield fds[fd], data
                else:
                    log.debug("Fd %d hangup/error, unregistering", fd)
                    discard(fd)
        if deadline:
            remaining = deadline - monotonic_time()
            if remaining <= 0:
                raise TimeoutExpired(p.pid)

    _wait(p, deadline)


def _wait(p, deadline=None):
    """
    Wait until process terminates, or if deadline is specified,
    `common.time.monotonic_time` exceeds deadline.

    Raises:
        `cmdutils.TimeoutExpired` if process did not terminate within
            deadline.
    """
    log.debug("Waiting for process (pid=%d)", p.pid)
    if deadline is None:
        p.wait()
    else:
        # We need to wait until deadline, Popen.wait() does not support
        # timeout. Python 3 is using busy wait in this case with a timeout of
        # 0.0005 seocnds. In vdsm we cannot allow such busy loops, and we don't
        # have a need to support very exact wait time. This loop uses
        # exponential backoff to detect termination quickly if the process
        # terminates quickly, and avoid busy loop if the process is stuck for
        # long time. Timeout will double from 0.0078125 to 1.0, and then
        # continue at 1.0 seconds, until deadline is reached.
        timeout = 1.0 / 256
        while p.poll() is None:
            remaining = deadline - monotonic_time()
            if remaining <= 0:
                raise TimeoutExpired(p.pid)
            time.sleep(min(timeout, remaining))
            if timeout < 1.0:
                timeout *= 2
    log.debug("Process (pid=%d) terminated", p.pid)
