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

from multiprocessing import Queue, Pipe, Process, current_process
from threading import Lock
import os
import signal
from functools import wraps
import logging
import select
import threading

import misc

from config import config
from logUtils import QueueHandler

MANAGE_PORT = config.getint("addresses", "management_port")
_log = logging.getLogger("ProcessPool")
LOGGING_THREAD_NAME = '__loggingThreadName__'

class Timeout(RuntimeError): pass
class NoFreeHelpersError(RuntimeError): pass
class PoolClosedError(RuntimeError): pass

class ProcessPoolLimiter(object):
    def __init__(self, procPool, limit):
        self._procPool = procPool
        self._limit = limit
        self._lock = threading.Lock()
        self._counter = 0

    def wrapFunction(self, func):
        @wraps(func)
        def wrapper(*args, **kwds):
            return self.runExternally(func, *args, **kwds)
        return wrapper

    def runExternally(self, *args, **kwargs):
        with self._lock:
            if self._counter >= self._limit:
                raise NoFreeHelpersError("You reached the process limit")

            self._counter += 1

        try:
            return self._procPool.runExternally(*args, **kwargs)
        finally:
            with self._lock:
                self._counter -= 1

class ProcessPoolMultiplexer(object):
    def __init__(self, processPool, helperPerUser):
        self._lock = threading.Lock()
        self._pool = processPool
        self._helpersPerUser = helperPerUser
        self._userDict = {}

    def __getitem__(self, key):
        try:
            return self._userDict[key]
        except KeyError:
            with self._lock:
                if key not in self._userDict:
                    self._userDict[key] = ProcessPoolLimiter(self._pool, self._helpersPerUser)
                return self._userDict[key]

class ProcessPool(object):
    def __init__(self, maxSubProcess, gracePeriod, timeout):
        self._maxSubProcess = maxSubProcess
        self._gracePeriod = gracePeriod
        self.timeout = timeout
        self._logQueue = Queue(-1)
        # We start all the helpers at once because of fork() semantics.
        # Every time you fork() the memory of the application is shared
        # with the child process until one of the processes writes to it.
        # In our case VDSM will probably rewrite the mem pretty quickly
        # and all the mem will just get wasted untouched on the child's side.
        # What we count on is having all the child processes share the mem.
        # This is best utilized by starting all child processes at once.
        self._helperPool = [Helper(self._logQueue) for i in range(self._maxSubProcess)]
        self._lockPool = [Lock() for i in range(self._maxSubProcess)]
        self._closed = False
        # Add thread for logging from oop
        self.logProc = threading.Thread(target=_helperLoggerLoop, args=(self._logQueue,))
        self.logProc.daemon = True
        self.logProc.start()

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
                helper = Helper(self._logQueue)
                self._helperPool[i] = helper

            kwargs[LOGGING_THREAD_NAME] = threading.current_thread().name
            helper.pipe.send((func, args, kwargs))

            pollres = misc.NoIntrPoll(helper.pipe.poll, self.timeout)

            if not pollres:
                helper.interrupt()

                pollres = misc.NoIntrPoll(helper.pipe.poll, self._gracePeriod)

                if not pollres:
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

def _helperLoggerLoop(logQueue):
    prevMessage = ''
    while True:
        try:
            record = logQueue.get()
            logger = logging.getLogger(record.name)
            # FIXME: Very, Very UGLY hack.
            # Somehow we get every log 3 times in queue.
            # So, because I still don't know the real reason
            # I just drop identical messages.
            # But, it very ugly and we need to find real reason for
            # such behavior
            if prevMessage == record.message:
                continue
            prevMessage = record.message

            logger.handle(record)
        except (KeyboardInterrupt, SystemExit):
            raise
        except Queue.Empty:
            pass
        except:
            pass

def disown(proc):
    # I know touching _children is wrong but there is no public API for
    # disowning a child
    current_process()._children.discard(proc)

class Helper(object):
    def __init__(self, logQueue):
        self._logQueue = logQueue
        self.lifeline, childsLifeline = os.pipe()
        self.pipe, hisPipe = Pipe()
        self.proc = Process(target=_helperMainLoop, args=(hisPipe, childsLifeline, self.lifeline, self._logQueue))
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
        os.close(self.lifeline)
        threading.Thread(target=terminationFlow).start()

    def interrupt(self):
        os.kill(self.proc.pid, signal.SIGINT)

def _releaseLoggingModuleLock():
    # As this is non public interface it might change. I would have logged a
    # warning but I can't
    if not hasattr(logging, "_releaseLock"):
        return

    # It's an RLock internally so we might have to release it multiple times
    while True:
        try:
            logging._releaseLock()
        except RuntimeError:
            break

def _helperMainLoop(pipe, lifeLine, parentLifelineFD, logQueue):
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
    FDSWHITELIST = (0, 1, 2, lifeLine, parentLifelineFD, pipe.fileno(),
                     logQueue._reader.fileno(), logQueue._writer.fileno())
    for fd in misc.getfds():
        if fd not in FDSWHITELIST:
            try:
                os.close(fd)
            except OSError:
                pass    # Nothing we can do

    # Add logger handler (via Queue)
    _releaseLoggingModuleLock()
    hdlr = QueueHandler(logQueue)
    hdlr.setLevel(_log.getEffectiveLevel())
    logging.root.handlers = [hdlr]
    for log in logging.Logger.manager.loggerDict.values():
        if hasattr(log, 'handlers'): log.handlers.append(hdlr)

    poller = select.poll()
    poller.register(lifeLine, 0) # Only SIGERR\SIGHUP
    poller.register(pipe.fileno(), select.EPOLLIN | select.EPOLLPRI)

    try:
        while True:

            for (fd, event) in poller.poll():
                # If something happened in lifeLine, it means that papa is gone
                # and we should go as well
                if fd == lifeLine or event in (select.EPOLLHUP, select.EPOLLERR):
                    return

            func, args, kwargs = pipe.recv()
            threading.current_thread().setName(kwargs[LOGGING_THREAD_NAME])
            kwargs.pop(LOGGING_THREAD_NAME)
            res = err = None
            try:
                res = func(*args, **kwargs)
            except KeyboardInterrupt as ex:
                err = ex
            except Exception as ex:
                err = ex

            pipe.send((res, err))
    except:
        # If for some reason communication with the host failed crash silently
        # There is no logging in oop and VDSM will handle it.
        pass

