#
# Copyright 2018 Red Hat, Inc.
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
import logging
import threading
import subprocess

from vdsm import utils
from vdsm.common import cmdutils
from vdsm.common import commands
from vdsm.common import exception

# Operation states

# Operation was created but not started yet
CREATED = "created"

# The operation was started
RUNNING = "running"

# The operation has terminated
TERMINATED = "terminated"

# Abort was called when the operation was running.
ABORTING = "aborting"

# The operation was aborted and is not running.
ABORTED = "aborted"

log = logging.getLogger("storage.operation")


class Command(object):
    """
    Simple storage command that does not support progress.
    """

    def __init__(self, cmd, cwd=None, nice=utils.NICENESS.HIGH,
                 ioclass=utils.IOCLASS.IDLE):
        self._cmd = cmd
        self._cwd = cwd
        self._nice = nice
        self._ioclass = ioclass
        self._lock = threading.Lock()
        self._state = CREATED
        self._proc = None

    def run(self):
        """
        Run a command, collecting data from the underlying process stdout and
        stderr, and returning the collected otuput.

        Data read from stderr is collected and will be included in the
        cmdutils.Error raised if the underlying command failed.

        Raises:
            `RuntimeError` if invoked more then once
            `exception.ActionStopped` if the command was aborted
            `cmdutils.Error` if the command failed
        """
        self._start_process()
        out, err = self._proc.communicate()
        self._finalize(out, err)
        return out

    def watch(self):
        """
        Run a command, iterating on data received from underlying command
        stdout.

        Data read from stderr is collected and will be included in the
        cmdutils.Error raised if the underlying command failed.

        Raises:
            `RuntimeError` if invoked more then once
            `exception.ActionStopped` if the command was aborted
            `cmdutils.Error` if the command failed
        """
        self._start_process()
        err = bytearray()
        for src, data in cmdutils.receive(self._proc):
            if src == cmdutils.OUT:
                yield data
            else:
                err += data
        self._finalize(b"", err)

    def abort(self):
        """
        Attempt to terminate the child process from another thread.

        Does not wait for the child process; the thread running this process
        will wait for the process. The caller must not assume that the
        operation was aborted when this returns.

        May be invoked multiple times.

        Raises:
            OSError if killing the underlying process failed.
        """
        with self._lock:
            if self._state == CREATED:
                log.debug("%s not started yet", self)
                self._state = ABORTED
            elif self._state == RUNNING:
                self._state = ABORTING
                log.info("Aborting %s", self)
                self._kill_process()
            elif self._state == ABORTING:
                log.info("Retrying abort %s", self)
                self._kill_process()
            elif self._state == TERMINATED:
                log.debug("%s has terminated", self)
            elif self._state == ABORTED:
                log.debug("%s was aborted", self)
            else:
                raise RuntimeError("Invalid state: %s" % self)

    def _start_process(self):
        """
        Start the underlying process.

        Raises:
            `RuntimeError` if invoked more then once
        """
        with self._lock:
            if self._state == ABORTED:
                raise exception.ActionStopped
            if self._state != CREATED:
                raise RuntimeError("Attempt to run an operation twice")
            self._proc = commands.start(
                self._cmd,
                cwd=self._cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                nice=self._nice,
                ioclass=self._ioclass)
            self._state = RUNNING

    def _finalize(self, out, err):
        """
        Update operation state after underlying process has terminated.

        Raises:
            `exception.ActionStopped` if the command was aborted
            `cmdutils.Error` if the command failed
            `RuntimeError` if operation state is invalid
        """
        rc = self._proc.returncode
        log.debug(cmdutils.retcode_log_line(rc, err))
        with self._lock:
            self._proc = None
            if self._state == ABORTING:
                self._state = ABORTED
                raise exception.ActionStopped
            elif self._state == RUNNING:
                self._state = TERMINATED
                if rc != 0:
                    raise cmdutils.Error(self._cmd, rc, out, err)
            else:
                raise RuntimeError("Invalid state: %s" % self)

    def _kill_process(self):
        """
        Must be called when holding the command lock.
        """
        if self._proc.poll() is not None:
            log.debug("%s has terminated", self)
            return
        try:
            self._proc.kill()
        except OSError as e:
            if e.errno != errno.ESRCH:
                raise
            log.debug("%s has terminated", self)

    def __repr__(self):
        s = "<Command {self._cmd} {self._state}, cwd={self._cwd} at {addr:#x}>"
        return s.format(self=self, addr=id(self))
