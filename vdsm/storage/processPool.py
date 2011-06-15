from multiprocessing import Pipe, Process
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
        self._helperPool = [None] * self._maxSubProcess
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

class Helper(object):
    def __init__(self):
        self.lifeline, childsLifeline = os.pipe()
        self.pipe, hisPipe = Pipe()
        self.proc = Process(target=_helperMainLoop, args=(hisPipe, childsLifeline, self.lifeline))
        self.proc.daemon = True
        self.proc.start()
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

