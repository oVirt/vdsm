#
# Copyright 2010-2012 Red Hat, Inc.
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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#

from struct import unpack, pack, calcsize
from threading import Lock
from time import time, sleep
import errno
import glob
import logging
import os
import pickle
import signal
import sys
import select
from contextlib import contextmanager

import misc
import fileUtils
import zombieReaper

# Crabs are known for their remote process calls
LENGTH_STRUCT_FMT = "Q"
LENGTH_STRUCT_LENGTH = calcsize(LENGTH_STRUCT_FMT)

if __name__ != "__main__":
    # If you don't have the vdsm package installed this will fail. Luckily we
    # don't need anything when the child spawns. Plus anything you don't have
    # to import is less memory taken by each helper.
    from vdsm.betterPopen import BetterPopen
    from vdsm import constants
else:
    # We add the parent directory so that imports that import the storage
    # package would work even though CWD is inside the storage package.
    sys.path.append(os.path.join(os.path.dirname(sys.argv[0]), "../"))


class Timeout(RuntimeError):
    pass


class CrabRPCServer(object):
    log = logging.getLogger("Storage.CrabRPCServer")

    def __init__(self, myRead, myWrite):
        self.rfile = os.fdopen(myRead, "r")
        self.wfile = os.fdopen(myWrite, "wa")
        self.registeredFunctions = {}
        self.registeredModules = {}

    def registerFunction(self, func, name=None):
        if name is None:
            name = func.__name__

        self.registeredFunctions[name] = func

    def registerModule(self, mod, name=None):
        if name is None:
            name = mod.__name__

        self.registeredModules[name] = mod

    def serve_forever(self):
        while True:
            try:
                self.serve_once()
            except:
                self.log.warn("Could not complete operation", exc_info=True)
                return

    def serve_once(self):
        rawLength = self.rfile.read(LENGTH_STRUCT_LENGTH)
        length = unpack(LENGTH_STRUCT_FMT, rawLength)[0]
        pickledCall = self.rfile.read(length)
        if len(pickledCall) < length:
            raise Exception("Pipe broke")

        name, args, kwargs = pickle.loads(pickledCall)
        err = res = None
        try:
            res = self.callRegisteredFunction(name, args, kwargs)
        except Exception, ex:
            err = ex

        resp = pickle.dumps((res, err))
        self.wfile.write(pack(LENGTH_STRUCT_FMT, len(resp)))
        self.wfile.write(resp)
        self.wfile.flush()

    def callRegisteredFunction(self, name, args, kwargs):
        if "." not in name:
            func = self.registeredFunctions[name]
        else:
            parts = name.split(".")
            func = parts.pop(0)
            func = self.registeredModules[func]
            for part in parts:
                func = getattr(func, part)

        return func(*args, **kwargs)


class CrabRPCProxy(object):
    log = logging.getLogger("Storage.CrabRPCProxy")

    def __init__(self, myRead, myWrite):
        self._myWrite = myWrite
        self._myRead = myRead
        misc.setNonBlocking(self._myWrite)
        misc.setNonBlocking(self._myRead)
        self._poller = select.poll()

    @contextmanager
    def _poll(self, fd, events, timeout):
        self._poller.register(fd, events)
        try:
            res = misc.NoIntrPoll(self._poller.poll, timeout)
            for fd, event in res:
                if event & (select.EPOLLERR | select.EPOLLHUP):
                    raise Timeout()
        finally:
            self._poller.unregister(fd)

    def _recvAll(self, length, timeout):
        startTime = time()
        rawResponse = ""
        while len(rawResponse) < length:
            timeLeft = timeout - (time() - startTime)
            if timeLeft <= 0:
                raise Timeout()

            if not self._poll(self._myRead, select.POLLIN | select.POLLPRI,
                    timeLeft):
                raise Timeout()

            try:
                rawResponse += os.read(self._myRead, length - len(rawResponse))
            except OSError as e:
                if e.errno not in (errno.EAGAIN, errno.EINTR):
                    raise

        return rawResponse

    def _sendAll(self, data, timeout):
        startTime = time()
        l = 0
        while l < len(data):
            timeLeft = timeout - (time() - startTime)
            if timeLeft <= 0:
                raise Timeout()

            if not self._poll(self._myWrite, select.POLLOUT,
                    timeLeft):
                raise Timeout()

            l += os.write(self._myWrite, data[l:])

    def callCrabRPCFunction(self, timeout, name, *args, **kwargs):
        request = pickle.dumps((name, args, kwargs))
        self._sendAll(pack(LENGTH_STRUCT_FMT, len(request)), timeout)
        self._sendAll(request, timeout)

        try:
            rawLength = self._recvAll(LENGTH_STRUCT_LENGTH, timeout)

            length = unpack(LENGTH_STRUCT_FMT, rawLength)[0]
            rawResponse = self._recvAll(length, timeout)
        except Timeout:
            raise
        except:
            # If for some reason the connection drops\gets out of sync we treat
            # it as a timeout so we only have one error path
            self.log.error("Problem with handler, treating as timeout",
                    exc_info=True)
            raise Timeout()

        res, err = pickle.loads(rawResponse)
        if err is not None:
            raise err

        return res

    def close(self):
        if not os:
            return

        if self._myWrite is not None:
            os.close(self._myWrite)
            self._myWrite = None

        if self._myRead is not None:
            os.close(self._myRead)
            self._myRead = None

    def __del__(self):
        self.close()


class PoolHandler(object):
    def __init__(self):
        myRead, hisWrite = os.pipe()
        hisRead, myWrite = os.pipe()

        try:
            # Some imports in vdsm assume /usr/share/vdsm is in your PYTHONPATH
            env = os.environ.copy()
            env['PYTHONPATH'] = "%s:%s" % (
                constants.P_VDSM, env.get("PYTHONPATH", ""))
            self.process = BetterPopen([constants.EXT_PYTHON, __file__,
                str(hisRead), str(hisWrite)], close_fds=False, env=env)

            self.proxy = CrabRPCProxy(myRead, myWrite)

        except:
            os.close(myWrite)
            os.close(myRead)
            raise
        finally:
            os.close(hisRead)
            os.close(hisWrite)

    def stop(self):
        try:
            os.kill(self.process.pid, signal.SIGKILL)
        except:
            pass

        zombieReaper.autoReapPID(self.process.pid)

    def __del__(self):
        self.stop()


class RemoteFileHandlerPool(object):
    log = logging.getLogger("Storage.RemoteFileHandler")

    def __init__(self, numOfHandlers):
        self._numOfHandlers = numOfHandlers
        self.handlers = [None] * numOfHandlers
        self.occupied = [Lock() for i in xrange(numOfHandlers)]

    def _isHandlerAvailable(self, poolHandler):
        if poolHandler is None:
            return False

        return True

    def callCrabRPCFunction(self, timeout, name, *args, **kwargs):
        for i, isOccupied in enumerate(self.occupied):
            if not isOccupied.acquire(False):
                continue

            try:
                handler = self.handlers[i]
                if not self._isHandlerAvailable(handler):
                    handler = self.handlers[i] = PoolHandler()

                return handler.proxy.callCrabRPCFunction(timeout, name,
                        *args, **kwargs)
            except Timeout:
                try:
                    self.handlers[i] = None
                    handler.stop()
                except:
                    self.log.error("Could not signal stuck handler (PID:%d)",
                            handler.process.pid, exc_info=True)

                self.handlers[i] = None
                raise

            finally:
                isOccupied.release()

        raise Exception("No free file handlers in pool")

    def close(self):
        for handler in self.handlers:
            if not self._isHandlerAvailable(handler):
                continue

            handler.stop()

    def __del__(self):
        self.close()


def simpleWalk(top, topdown=True, onerror=None, followlinks=False):
    # We need this _simpleWalk wrapper because of regular os.walk return
    # iterator and we can't use it in oop.
    filesList = []
    for base, dirs, files in os.walk(top, topdown, onerror, followlinks):
        for f in files:
            filesList.append(os.path.join(base, f))
    return filesList


def directReadLines(path):
    with fileUtils.open_ex(path, "dr") as f:
        return f.readlines()


def directWriteLines(path, lines):
    with fileUtils.open_ex(path, "dw") as f:
        return f.writelines(lines)


def createSparseFile(path, size, mode=None):
    with open(path, "w") as f:
        if mode is not None:
            os.chmod(path, mode)
        f.truncate(size)


def readLines(path):
    with open(path, "r") as f:
        return f.readlines()


def writeLines(path, lines):
    with open(path, "w") as f:
        return f.writelines(lines)


def echo(data):
    """Echo data, used for testing"""
    return data


def parseArgs():
    try:
        myRead, myWrite = sys.argv[1:]
        myRead = int(myRead)
        myWrite = int(myWrite)
    except ValueError, ex:
        sys.stderr.write("Error parsing args %s\n" % ex)
        sys.exit(errno.EINVAL)

    return myRead, myWrite


def closeFDs(whitelist):
    for fd in misc.getfds():
        if fd in whitelist:
            continue

        while True:
            try:
                os.close(fd)
                break
            except (OSError, IOError) as e:
                if e.errno in (errno.EINTR, errno.EAGAIN):
                    continue

                if e.errno == errno.EBADF:
                    break

                raise


if __name__ == "__main__":
    try:
        try:
            myRead, myWrite = parseArgs()
            closeFDs((myRead, myWrite, 2))
        except:
            logging.root.error("Error in prexecution", exc_info=True)
            raise

        try:
            server = CrabRPCServer(myRead, myWrite)
            for func in (writeLines, readLines, createSparseFile, echo, sleep,
                    directWriteLines, directReadLines, simpleWalk):

                server.registerFunction(func)

            for mod in (os, glob, fileUtils):
                server.registerModule(mod)
        except Exception, ex:
            logging.root.error("Error creating CrabRPC server", exc_info=True)
            raise

        try:
            server.serve_forever()
        except Exception, ex:
            logging.root.error("Error while serving", exc_info=True)
            raise

    except BaseException as e:
        sys.exit(errno.ENOEXEC)
