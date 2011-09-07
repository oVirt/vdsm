#
# Copyright 2011 Red Hat, Inc.
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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301  USA
#
# Refer to the README and COPYING files for full details of the license
#

from multiprocessing import Pipe, Process, current_process
from threading import Lock
import os
import signal
from functools import wraps
import logging
import select
import threading

import misc

from config import config

MANAGE_PORT = config.getint("addresses", "management_port")

class Timeout(RuntimeError): pass
class NoFreeHelpersError(RuntimeError): pass
class PoolClosedError(RuntimeError): pass

class ProcessPool(object):
    def __init__(self, maxSubProcess, gracePeriod, timeout):
        self._log = logging.getLogger("ProcessPool")
        self._maxSubProcess = maxSubProcess
        self._gracePeriod = gracePeriod
        self.timeout = timeout
        # We start all the helpers at once because of fork() semantics.
        # Every time you fork() the memory of the application is shared
        # with the child process until one of the processes writes to it.
        # In our case VDSM will probably rewrite the mem pretty quickly
        # and all the mem will just get wasted untouched on the child's side.
        # What we count on is having all the child processes share the mem.
        # This is best utilized by starting all child processes at once.
        self._helperPool = [Helper() for i in range(self._maxSubProcess)]
        self._lockPool = [Lock() for i in range(self._maxSubProcess)]
        self._closed = False

    def wrapFunction(self, func):
        @wraps(func)
        def wrapper(*args, **kwds):
            return self.runExternally(func, *args, **kwds)
        return wrapper

    def runExternally(self, func, *args, **kwargs):
        if self._closed:
            raise PoolClosedError()

        lockAcquired = False
        for i, lock in enumerate(self._lockPool):
            if lock.acquire(False):
                lockAcquired = True
                break

        if not lockAcquired:
            raise NoFreeHelpersError("No free processes")

        try:
            helper = self._helperPool[i]
            if helper is None:
                helper = Helper()
                self._helperPool[i] = helper

            helper.pipe.send((func, args, kwargs))
            if not helper.pipe.poll(self.timeout):
                helper.interrupt()
                if not helper.pipe.poll(self._gracePeriod):
                    helper.kill()
                    self._helperPool[i] = None
                    raise Timeout("Operation Stuck")

            res, err = helper.pipe.recv()

            if err is not None:
                # Keyboard interrupt is never thrown in regular use
                # if it was thrown it is probably me
                if err is KeyboardInterrupt:
                    raise Timeout("Operation Stuck (But snapped out of it)")
                raise err

            return res
        finally:
            lock.release()

    def close(self):
        if self._closed:
            return
        self._closed = True
        for i, lock in enumerate(self._lockPool):
            lock.acquire()
            helper = self._helperPool[i]
            if helper is not None:
                os.close(helper.lifeline)
                try:
                    os.waitpid(helper.proc.pid, os.WNOHANG)
                except OSError:
                    pass


        # The locks remain locked of purpose so no one will
        # be able to run further commands

def disown(proc):
    # I know touching _children is wrong but there is no public API for
    # disowning a child
    current_process()._children.discard(proc)

class Helper(object):
    def __init__(self):
        self.lifeline, childsLifeline = os.pipe()
        self.pipe, hisPipe = Pipe()
        self.proc = Process(target=_helperMainLoop, args=(hisPipe, childsLifeline, self.lifeline))
        self.proc.daemon = True
        self.proc.start()
        disown(self.proc)

        os.close(childsLifeline)

    def kill(self):
        def terminationFlow():
            try:
                self.proc.terminate()
            except:
                pass
            if not self.proc.is_alive():
                self.proc.join()
                return
            try:
                os.kill(self.proc.pid, signal.SIGKILL)
            except:
                pass
            self.proc.join()
        threading.Thread(target=terminationFlow).start()

    def interrupt(self):
        os.kill(self.proc.pid, signal.SIGINT)

def _helperMainLoop(pipe, lifeLine, parentLifelineFD):
    os.close(parentLifelineFD)

    # Restoring signal handlers that might deadlock on prepareForShutdown
    # in the children.
    # This must be improved using pthread_sigmask before forking so that
    # we don't risk to have a race condition.
    for signum in (signal.SIGTERM, signal.SIGUSR1):
        signal.signal(signum, signal.SIG_DFL)

    # Removing all the handlers from the loggers. This avoid a deadlock on
    # the logging locks. Multi-process and multi-threading don't mix well.
    #   - BZ#732652: https://bugzilla.redhat.com/show_bug.cgi?id=732652
    #   - I6721: http://bugs.python.org/issue6721
    for log in logging.Logger.manager.loggerDict.values():
        if hasattr(log, 'handlers'): del log.handlers[:]

    # Close all file-descriptors we don't need
    # Logging won't work past this point
    for fd in misc.getfds():
        if fd not in (0, 1, 2, lifeLine, parentLifelineFD, pipe.fileno()):
            try:
                os.close(fd)
            except OSError:
                pass    # Nothing we can do

    poller = select.poll()
    poller.register(lifeLine, 0) # Only SIGERR\SIGHUP
    poller.register(pipe.fileno(), select.EPOLLIN | select.EPOLLPRI)

    while True:

        for (fd, event) in poller.poll():
            # If something happened in lifeLine, it means that papa is gone
            # and we should go as well
            if fd == lifeLine or event in (select.EPOLLHUP, select.EPOLLERR):
                return

        func, args, kwargs = pipe.recv()
        res = err = None
        try:
            res = func(*args, **kwargs)
        except KeyboardInterrupt as ex:
            err = ex
        except Exception as ex:
            err = ex

        pipe.send((res, err))

