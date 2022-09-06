# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division
import errno
import os
import select
import signal

from vdsm.common import filecontrol

_signal_poller = None
_signal_read_fd = None


def register():
    '''
    This function creates a select.poll object that can be used in the same
    manner as signal.pause(). The poll object returns each time a signal was
    received by the process.

    This function has to be called from the main thread.
    '''

    global _signal_poller
    global _signal_read_fd

    if _signal_poller is not None:
        raise RuntimeError('register was already called')

    read_fd, write_fd = os.pipe()

    # Python c-level signal handler requires that the write end will be in
    # non blocking mode
    filecontrol.set_non_blocking(write_fd)

    # Set the read pipe end to non-blocking too, just in case.
    filecontrol.set_non_blocking(read_fd)

    # Prevent subproccesses we execute from inheriting the pipes.
    filecontrol.set_close_on_exec(write_fd)
    filecontrol.set_close_on_exec(read_fd)

    signal.set_wakeup_fd(write_fd)

    poller = select.poll()
    poller.register(read_fd, select.POLLIN)

    _signal_poller = poller
    _signal_read_fd = read_fd


def wait_for_signal(timeout=None):
    '''
    This function acts like signal.pause(), it returns after a signal was
    received and handled. Unlike signal.pause(), it will wake up even if other
    thread caught the signal while this function was called.
    A timeout can be specified to avoid waiting indefinitely. Provide timeout
    in seconds.

    This function has to be called from the main thread.
    '''

    if _signal_poller is None:
        raise RuntimeError("Attempt to wait on signal before calling register")

    # poll() timeout is in milliseconds
    if timeout is not None:
        timeout *= 1000

    try:
        cleanup = [] != _signal_poller.poll(timeout)
    except select.error as e:
        if e.args[0] != errno.EINTR:
            raise
        cleanup = True

    if cleanup:
        _empty_pipe()


def _read(fd, length):
    """
    Read that handles recoverable exceptions.
    """
    while True:
        try:
            return os.read(fd, length)
        except OSError as e:
            if e.errno == errno.EINTR:
                continue
            elif e.errno == errno.EAGAIN:
                return ''
            raise


def _empty_pipe():
    while len(_read(_signal_read_fd, 128)) == 128:
        pass
