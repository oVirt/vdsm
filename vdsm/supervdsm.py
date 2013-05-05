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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston,
# MA 02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#

import os
from multiprocessing import AuthenticationError
from multiprocessing.managers import BaseManager
import logging
import threading
import uuid
from errno import ENOENT, ESRCH

import storage.misc as misc
from vdsm import constants, utils

_g_singletonSupervdsmInstance = None
_g_singletonSupervdsmInstance_lock = threading.Lock()


def __supervdsmServerPath():
    base = os.path.dirname(__file__)

    # serverFile can be both the py or pyc file. In oVirt node we don't keep
    # py files. this method looks for one of the two to calculate the absolute
    # path of supervdsmServer
    for serverFile in ("supervdsmServer.py", "supervdsmServer.pyc"):
        serverPath = os.path.join(base, serverFile)
        if os.path.exists(serverPath):
            return os.path.abspath(serverPath)

    raise RuntimeError("SuperVDSM Server not found")

PIDFILE = os.path.join(constants.P_VDSM_RUN, "svdsm.pid")
TIMESTAMP = os.path.join(constants.P_VDSM_RUN, "svdsm.time")
ADDRESS = os.path.join(constants.P_VDSM_RUN, "svdsm.sock")
SUPERVDSM = __supervdsmServerPath()

extraPythonPathList = []


class _SuperVdsmManager(BaseManager):
    pass


class ProxyCaller(object):

    def __init__(self, supervdsmProxy, funcName):
        self._funcName = funcName
        self._supervdsmProxy = supervdsmProxy

    def __call__(self, *args, **kwargs):
        callMethod = lambda: \
            getattr(self._supervdsmProxy._svdsm, self._funcName)(*args,
                                                                 **kwargs)
        if not self._supervdsmProxy.isRunning():
            # getting inside only when svdsm is down. its rare case so we
            # don't care that isRunning will run twice
            with self._supervdsmProxy.proxyLock:
                if not self._supervdsmProxy.isRunning():
                    self._supervdsmProxy.launch()

        try:
            return callMethod()
        # handling internal exception that we raise to identify supervdsm
        # validation. only this exception can cause kill!
        except AuthenticationError:
            with self._supervdsmProxy.proxyLock:
                self._supervdsmProxy.kill()
                self._supervdsmProxy.launch()
            return callMethod()


class SuperVdsmProxy(object):
    """
    A wrapper around all the supervdsm init stuff
    """
    _log = logging.getLogger("SuperVdsmProxy")

    def __init__(self):
        self.proxyLock = threading.Lock()
        self._firstLaunch = True

        # Declaration of public variables that keep files' names that svdsm
        # uses. We need to be able to change these variables so that running
        # tests doesn't disturb and already running VDSM on the host.
        self.setIPCPaths(PIDFILE, TIMESTAMP, ADDRESS)

    def setIPCPaths(self, pidfile, timestamp, address):
        self.pidfile = pidfile
        self.timestamp = timestamp
        self.address = address

    def open(self, *args, **kwargs):
        return self._manager.open(*args, **kwargs)

    def _cleanOldFiles(self):
        self._log.debug("Cleanning svdsm old files: %s, %s, %s",
                        self.pidfile, self.timestamp, self.address)
        for f in (self.pidfile, self.timestamp, self.address):
            utils.rmFile(f)

    def _start(self):
        self._authkey = str(uuid.uuid4())
        self._log.debug("Launching Super Vdsm")

        # we pass to svdsm filenames and uid. Svdsm will use those filenames
        # to create its internal files and give to the passed uid the
        # permissions to read those files.
        superVdsmCmd = [constants.EXT_PYTHON, SUPERVDSM,
                        self._authkey, str(os.getpid()),
                        self.pidfile, self.timestamp, self.address,
                        str(os.getuid())]

        p = utils.execCmd(superVdsmCmd, sync=False, sudo=True)
        p.wait(2)
        if p.returncode:
            utils.panic('executing supervdsm failed')

    def kill(self):
        try:
            with open(self.pidfile, "r") as f:
                pid = int(f.read().strip())
            utils.execCmd([constants.EXT_KILL, "-9", str(pid)], sudo=True)
        except Exception:
            self._log.error("Could not kill old Super Vdsm %s",
                            exc_info=True)

        self._cleanOldFiles()
        self._authkey = None
        self._manager = None
        self._svdsm = None
        self._firstLaunch = True

    def isRunning(self):
        if self._firstLaunch or self._svdsm is None:
            return False

        try:
            with open(self.pidfile, "r") as f:
                spid = f.read().strip()
            with open(self.timestamp, "r") as f:
                createdTime = f.read().strip()
        except IOError as e:
            # pid file and timestamp file must be exist after first launch,
            # otherwise excpetion will be raised to svdsm caller
            if e.errno == ENOENT and self._firstLaunch:
                return False
            else:
                raise

        try:
            pTime = str(misc.getProcCtime(spid))
        except OSError as e:
            if e.errno == ESRCH:
                # Means pid is not exist, svdsm was killed
                return False
            else:
                raise

        if pTime == createdTime:
            return True
        else:
            return False

    def _connect(self):
        self._manager = _SuperVdsmManager(address=self.address,
                                          authkey=self._authkey)
        self._manager.register('instance')
        self._manager.register('open')
        self._log.debug("Trying to connect to Super Vdsm")
        try:
            self._manager.connect()
        except Exception as ex:
            self._log.warn("Connect to svdsm failed %s", ex)
            raise
        self._svdsm = self._manager.instance()

    def launch(self):
        self._firstLaunch = False
        self._start()
        try:
            # We retry 3 times to connect to avoid exceptions that are raised
            # due to the process initializing. It might takes time to create
            # the communication socket or other initialization methods take
            # more time than expected.
            utils.retry(self._connect, Exception, timeout=60, tries=3)
        except:
            misc.panic("Couldn't connect to supervdsm")

    def __getattr__(self, name):
        return ProxyCaller(self, name)


def getProxy():
    global _g_singletonSupervdsmInstance
    if _g_singletonSupervdsmInstance is None:
        with _g_singletonSupervdsmInstance_lock:
            if _g_singletonSupervdsmInstance is None:
                _g_singletonSupervdsmInstance = SuperVdsmProxy()
    return _g_singletonSupervdsmInstance
