#
# Copyright 2014-2019 Red Hat, Inc.
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
import logging
import os
import random
import string
import subprocess
import sys
import time

import six
from six.moves import range

"""
shared utilities and common code for the virt package
"""

import os.path
import threading

from vdsm.common import cmdutils, supervdsm
from vdsm.common.commands import start, terminating
from vdsm.common.fileutils import rm_file
from vdsm.common.time import monotonic_time
from vdsm.constants import P_VDSM_LOG


_COMMANDS_LOG_DIR = os.path.join(P_VDSM_LOG, 'commands')

log = logging.getLogger('virt.utils')


def isVdsmImage(drive):
    """
    Tell if drive looks like a vdsm image

    :param drive: drive to check
    :type drive: dict or vm.Drive
    :return: bool
    """
    required = ('domainID', 'imageID', 'poolID', 'volumeID')
    return all(k in drive for k in required)


class ItemExpired(KeyError):
    pass


class ExpiringCache(object):
    """
    ExpiringCache behaves like a dict, but an expiration time
    is attached to each key. Thread safe.

    Parameters:
    ttl: items will expire ttl seconds after they were inserted.
    clock: time.time() or monotonic_time()-like callable.

    Expired keys are removed on the first attempt to access them.
    """
    def __init__(self, ttl, clock=monotonic_time):
        self._ttl = ttl
        self._clock = clock
        self._items = {}
        self._lock = threading.Lock()

    def get(self, key, default=None):
        try:
            return self._get_live(key)
        except KeyError:
            return default

    def clear(self):
        with self._lock:
            self._items.clear()

    def __setitem__(self, key, value):
        item = self._clock() + self._ttl, value
        with self._lock:
            self._items[key] = item

    def __getitem__(self, key):
        """
        If no value is stored for key, raises KeyError.
        If an item expired, raises ItemExpired (a subclass of KeyError).
        """
        return self._get_live(key)

    def __delitem__(self, key):
        with self._lock:
            del self._items[key]

    def __bool__(self):
        now = self._clock()
        with self._lock:
            expired_keys = [
                key for key, (expiration, _)
                in six.iteritems(self._items)
                if expiration <= now]
            for key in expired_keys:
                del self._items[key]

            return bool(self._items)

    # pylint: disable=nonzero-method
    def __nonzero__(self):  # TODO: drop when py2 is no longer needed
        return self.__bool__()

    # private

    def _get_live(self, key):
        now = self._clock()
        with self._lock:
            expiration, value = self._items[key]

            if expiration <= now:
                del self._items[key]
                raise ItemExpired

            return value


def cleanup_guest_socket(sock):
    if sock is None:
        return
    if os.path.islink(sock):
        rm_file(os.path.realpath(sock))
    rm_file(sock)


class DynamicBoundedSemaphore(object):
    """
    Bounded Semaphore with the additional ability
    to dynamically adjust its `bound`.

    The semaphore can be acquired only if the current number of acquisitions
    is strictly smaller than the current `bound` value.

    Specifically when "throttling" the semaphore below the current number
    of concurrent acquisitions, the semaphore will become acquirable again
    only after the semaphore is released by its current users appropriate
    number of times - so the '# of acquisitions' will become smaller than the
    semaphore's new `bound`.
    """

    def __init__(self, value):
        self._cond = threading.Condition(threading.Lock())
        self._value = value
        self._bound = value

    def acquire(self, blocking=True):
        """ Same behavior as threading.BoundedSemaphore.acquire """
        rc = False
        with self._cond:
            # to enable runtime adjustment of semaphore bound
            # we allow the _value counter to reach negative values
            while self._value <= 0:
                if not blocking:
                    break
                self._cond.wait()
            else:
                self._value -= 1
                rc = True
        return rc

    __enter__ = acquire

    def release(self):
        """ Same behavior as threading.BoundedSemaphore.release """
        with self._cond:
            if self._value >= self._bound:
                raise ValueError("Dynamic Semaphore released too many times")
            self._value += 1
            self._cond.notify()

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.release()

    @property
    def bound(self):
        return self._bound

    @bound.setter
    def bound(self, value):
        """ Dynamically updates semaphore bound.

        When the specified value is larger than the previous bound,
        it releases the semaphore the required number of times (and possibly
        wakes that number of waiting threads).

        When the specified value is smaller than the previous bound,
        it simply decreases the current value, which may in doing so become
        negative. Semaphore with value <= 0 is considered unavailable and
        appropriate number of `release()` calls or a new `bound = n` is
        required to make it obtainable again.

        """
        with self._cond:
            delta = value - self._bound
            self._bound = value
            if delta < 0:
                self._value += delta

        # implementation note:
        # if we are increasing the bound we need to do this outside of
        # context manager otherwise release() would deadlock since it also
        # tries to obtain the lock
        if delta > 0:
            for i in range(delta):
                self.release()


def extract_cluster_version(md_values):
    cluster_version = md_values.get('clusterVersion')
    if cluster_version is not None:
        return [int(v) for v in cluster_version.split('.')]
    return None


class TeardownError(Exception):
    pass


class prepared(object):
    """
    A context manager to prepare a group of objects (for example, disk images)
    for an operation.
    """

    def __init__(self, images):
        """
        Receives a variable number of images which must implement
        prepare() and teardown() methods.
        """
        self._images = images
        self._prepared_images = []

    def __enter__(self):
        for image in self._images:
            try:
                image.prepare()
            except:
                exc = sys.exc_info()
                log.error("Error preparing image %r", image)
                try:
                    self._teardown()
                except TeardownError:
                    log.exception("Error tearing down images")
                try:
                    six.reraise(*exc)
                finally:
                    del exc

            self._prepared_images.append(image)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            self._teardown()
        except TeardownError:
            if exc_type is None:
                raise
            # Don't hide the original error
            log.exception("Error tearing down images")

    def _teardown(self):
        errors = []
        while self._prepared_images:
            image = self._prepared_images.pop()
            try:
                image.teardown()
            except Exception as e:
                errors.append(e)
        if errors:
            raise TeardownError(errors)


class LockTimeout(RuntimeError):

    def __init__(self, timeout, lockid, flow):
        self.timeout = timeout
        self.lockid = lockid
        self.flow = flow

    def __str__(self):
        msg = 'waiting more than {elapsed}s for lock {lockid} held by {flow}'
        return msg.format(
            elapsed=self.timeout, lockid=self.lockid, flow=self.flow
        )


class TimedAcquireLock(object):

    def __init__(self, lockid):
        self._lockid = lockid
        self._lock = threading.Lock()
        self._flow = None

    @property
    def holder(self):
        return self._flow

    def acquire(self, timeout, flow=None):
        end = time.time() + timeout
        while not self._lock.acquire(False):
            time.sleep(0.1)
            if time.time() > end:
                raise LockTimeout(timeout, self._lockid, self._flow)

        self._flow = flow

    def release(self):
        self._flow = None
        self._lock.release()


def sasl_enabled():
    """
    Returns true if qemu.conf contains entry for SASL authentication
    for VNC console.
    """
    return supervdsm.getProxy().check_qemu_conf_contains('vnc_sasl', '1')


class LoggingError(cmdutils.Error):
    msg = ('Command {self.cmd} failed with rc={self.rc} log={self.log_path!r}')

    def __init__(self, cmd, rc, log_path):
        super().__init__(cmd, rc, None, None)
        self.log_path = log_path


def run_logging(args, log_tag=None):
    """
    Start a command storing its stdout/stderr into a file, communicate with it,
    and wait until the command terminates. Ensures that the command is killed
    if an unexpected error is raised. Note that since the stdout/stderr is
    redirected to the file it is not piped to VDSM.

    args are logged when command starts, and are included in the exception if a
    command has failed. If args contain sensitive information that should not
    be logged, such as passwords, they must be wrapped with ProtectedPassword.
    While access to the directory with log files is restricted caution should
    be taken when logging commands. Avoid storing output of commands that may
    leak passwords or other sensitive information.

    The child process stdout and stderr are always buffered. If you have
    special needs, such as running the command without buffering stdout, or
    create a pipeline of several commands, use the lower level start()
    function.

    If log_tag is not used the log file name is
    'virtcmd-<command>-<datetime>-<random_string>.log'. If
    log_tag is used the format is
    'virtcmd-<command>-<log_tag>-<datetime>-<random_string>.log'.

    The granularity of <datetime> part of the file name is one second. To
    minimize file collision there is a random string of characters appended to
    the name.

    Arguments:
        args (list): Command arguments
        log_tag (str): Optional string to be included in log file name to
            better distinguish the log files and avoid potential name
            collisions. This could be for example VM ID.

    Returns:
        Path to the log file.

    Raises:
        LoggingError if the command terminated with a non-zero exit code.
        TerminatingFailure if command could not be terminated.
    """
    stdout = subprocess.DEVNULL
    stderr = subprocess.DEVNULL
    cmd_log_file = None

    timestamp = time.strftime('%Y%m%dT%H%M%S')
    command = os.path.basename(str(args[0]))
    log_tag = '' if log_tag is None else '-%s' % log_tag
    suffix = ''.join(random.choice(string.ascii_lowercase) for _ in range(4))
    cmd_log_path = os.path.join(
        _COMMANDS_LOG_DIR, 'virtcmd-%s%s-%s-%s.log' % (
            command, log_tag, timestamp, suffix))
    try:
        os.makedirs(_COMMANDS_LOG_DIR, mode=0o750, exist_ok=True)
        cmd_log_file = os.open(
            cmd_log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, mode=0o640)
    except OSError:
        log.exception('Failed to open log file')
        cmd_log_path = None
    else:
        stdout = cmd_log_file
        stderr = cmd_log_file
        log.debug('Running command %r with logs in %s', args, cmd_log_path)

    p = start(args,
              stdin=subprocess.PIPE,
              stdout=stdout,
              stderr=stderr)

    with terminating(p):
        p.communicate()

    log.debug(cmdutils.retcode_log_line(p.returncode))

    if cmd_log_file is not None:
        os.close(cmd_log_file)
    if p.returncode != 0:
        raise LoggingError(args, p.returncode, cmd_log_path)

    return cmd_log_path


class LibguestfsCommand(object):
    def __init__(self, path):
        self._args = [path, '-v', '-x']

    def run(self, args_, log_tag=None):
        args = self._args + args_
        run_logging(args, log_tag=log_tag)
