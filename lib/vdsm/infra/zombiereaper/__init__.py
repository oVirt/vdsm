#
# Copyright 2014 Hat, Inc.
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
ZombieReaper is a module to handle the annoying problem of cleaning up child
processes. Specifically, for cases where the result of the child process is not
needed.

This replaces the common idiom of running a thread which only does
:py:meth:`subprocess.Popen.wait()`, saving precious threads.
"""

import os
import signal

_trackedPids = set()


def autoReapPID(pid):
    """
    Register a PID to be auto-cleaned.
    """
    _trackedPids.add(pid)
    # SIGCHLD happend before we added the pid to the set
    _tryReap(pid)


def _tryReap(pid):
        try:
            pid, rv = os.waitpid(pid, os.WNOHANG)
            if pid != 0:
                _trackedPids.discard(pid)
        except OSError:
            _trackedPids.discard(pid)


def _zombieReaper(signum, frame):
    for pid in _trackedPids.copy():
        _tryReap(pid)


def registerSignalHandler():
    """
    Set up the signal handler so that PIDs are reaped. Should be called once
    at the start of the program.
    """
    signal.signal(signal.SIGCHLD, _zombieReaper)


def unregisterSignalHandler():
    """
    Stop cleaning PIDs. Should only be used for testing or other specialized
    use cases.
    """
    signal.signal(signal.SIGCHLD, signal.SIG_DFL)
