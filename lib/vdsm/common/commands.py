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
import logging

from vdsm.common import cmdutils
from vdsm.common import constants
from vdsm.common import password
from vdsm.common.cmdutils import command_log_line, retcode_log_line
from vdsm.common.compat import subprocess
from vdsm.common.marks import deprecated

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


def communicate(proc, input=None):
    """
    A wrapper for subprocess.communicate() which waits for process to be
    finished and logs the returned code (and error output if any).

    Arguments:
        proc: subprocess.Popen instance or commands.PrivilegedPopen if
            subprocess was created with sudo enabled.
        input (bytes): input data to be sent to the child process, or None, if
            no data should be sent to the process.

    Returns:
        Tuple of process standard output and error output.
    """
    with terminating(proc):
        out, err = proc.communicate(input)

    log.debug(cmdutils.retcode_log_line(proc.returncode, err=err))

    return out, err


@deprecated
def execCmd(command, sudo=False, cwd=None, data=None, raw=False,
            printable=None, env=None, nice=None, ioclass=None,
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

    with terminating(p):
        (out, err) = p.communicate(data)

    if out is None:
        # Prevent splitlines() from barfing later on
        out = b""

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
