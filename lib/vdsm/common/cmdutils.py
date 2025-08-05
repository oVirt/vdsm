# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

import errno
import io
import logging
import os
import re
import select
import shutil
import subprocess
import time

from vdsm.common import constants
from vdsm.common import errors
from vdsm.common import osutils
from vdsm.common.config import config
from vdsm.common.password import ProtectedPassword
from vdsm.common.time import monotonic_time

log = logging.getLogger("procutils")

# receive() source names
OUT = "out"
ERR = "err"

_ANY_CPU = ["0-%d" % (os.sysconf('SC_NPROCESSORS_CONF') - 1)]
_SUDO_NON_INTERACTIVE_FLAG = "-n"
_USING_CPU_AFFINITY = config.get('vars', 'cpu_affinity') != ""


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
                    self._cmd = shutil.which(self.name)
                if self._cmd is None:
                    raise OSError(errno.ENOENT,
                                  os.strerror(errno.ENOENT) + ': ' +
                                  self.name)
        return self._cmd

    def __repr__(self):
        return str(self.cmd)

    def __str__(self):
        return str(self.cmd)


def command_log_line(args, cwd=None):
    return "{0} (cwd {1})".format(_list2cmdline(args), cwd)


def retcode_log_line(code, err=None):
    result = "SUCCESS" if code == 0 else "FAILED"
    return "{0}: <err> = {1!r}; <rc> = {2!r}".format(result, err, code)


def _list2cmdline(args):
    """
    Convert argument list for exeCmd to string for logging. 'ProtectedPassword'
    arguments are obfuscated so that no secrets leak. The purpose of this
    log is make it easy to run vdsm commands in the shell for debugging.
    """
    parts = []
    for arg in args:
        if isinstance(arg, ProtectedPassword):
            arg = str(arg)
        if _needs_quoting(arg) or arg == '':
            arg = "'" + arg.replace("'", r"'\''") + "'"
        parts.append(arg)
    return ' '.join(parts)


# This function returns truthy value if its argument contains unsafe characters
# for including in a command passed to the shell. The safe characters were
# stolen from pipes._safechars.
_needs_quoting = re.compile(r'[^A-Za-z0-9_%+,\-./:=@]').search


def exec_cmd(cmd, env=None):
    """
    Execute cmd in an external process, collect its output and returncode

    :param cmd: an iterator of strings to be passed as exec(2)'s argv
    :param env: an optional dictionary to be placed as environment variables
                of the external process. If None, the environment of the
                calling process is used.
    :returns: a 3-tuple of the process's
              (returncode, stdout content, stderr content.)

    This is a bare-bones version of `commands.execCmd`. Unlike the latter, this
    function
    * uses Vdsm cpu pinning, and must not be used for long CPU-bound processes.
    * does not guarantee to kill underlying process if Popen.communicate()
      raises. Commands that access shared storage may not use this api.
    * does not hide passwords in logs if they are passed in cmd
    """
    logging.debug(command_log_line(cmd))

    p = subprocess.Popen(
        cmd, close_fds=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env=env)

    out, err = p.communicate()

    logging.debug(retcode_log_line(p.returncode, err=err))

    return p.returncode, out, err


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
            subprocess.Popen or subprocess32.Popen or cpopen.CPopen.
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
        remaining_msec = int(remaining * 1000) if deadline and remaining is not None else None
        try:
            ready = poller.poll(remaining_msec)
        except select.error as e:
            if e.args[0] != errno.EINTR:
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
    command = [constants.EXT_SUDO, _SUDO_NON_INTERACTIVE_FLAG]
    command.extend(cmd)
    return command


def taskset(cmd, cpu_list):
    command = [constants.EXT_TASKSET, "--cpu-list", ",".join(cpu_list)]
    command.extend(cmd)
    return command


def prlimit(cmd, cpu_time=None, address_space=None):
    """
    Wrap cmd with prlimit, limiting resource usage.

    Arguments:
        cpu_time (int): Limit command cpu time in seconds. If the command
            exceeds this value it will be terminated by SIGKILL.
        address_space (int): Limit command address space size in bytes. If the
            command tries to allocate too much memory, the allocation will
            fail.

    NOTE: Limiting command resident size (--rss=N) seems to be broken with
    prlimit. The limit is applied but has no effect on memory usage.
    """
    command = [constants.EXT_PRLIMIT]
    # NOTE: long options require --key=value format.
    if cpu_time:
        command.append("--cpu=%d" % cpu_time)
    if address_space:
        command.append("--as=%d" % address_space)
    command.extend(cmd)
    return command
