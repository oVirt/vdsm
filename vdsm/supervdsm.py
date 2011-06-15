import os
from multiprocessing.managers import BaseManager
import logging
import threading
import uuid
from time import sleep
import socket

import storage.misc as misc
import constants

_g_singletonSupervdsmInstance = None
_g_singletonSupervdsmInstance_lock = threading.Lock()

PIDFILE = "/var/run/vdsm/svdsm.pid"
ADDRESS = "/var/run/vdsm/svdsm.sock"
SUPERVDSM = os.path.join(os.path.dirname(__file__), "supervdsmServer.pyc")
SUPERVDSM = os.path.abspath(SUPERVDSM)
class _SuperVdsmManager(BaseManager): pass

class ProxyCaller(object):
    def __init__(self, supervdsmProxy, funcName):
        self._funcName = funcName
        self._supervdsmProxy = supervdsmProxy
    def __call__(self, *args, **kwargs):
        callMethod = lambda : getattr(self._supervdsmProxy._svdsm, self._funcName)(*args, **kwargs)
        try:
            return callMethod()
        except (IOError, socket.error):
            self._supervdsmProxy._restartSupervdsm()
            return callMethod()

class SuperVdsmProxy(object):
    """
    A wrapper around all the supervdsm init stuff
    """
    _log = logging.getLogger("SuperVdsmProxy")

    def __init__(self):
        # Kill supervdsm from previous session (if exists), and launch a new one
        self._restartSupervdsm()

        self._log.debug("Connected to Super Vdsm")

    def open(self, *args, **kwargs):
        return self._manager.open(*args, **kwargs)

    def _launchSupervdsm(self):
        self._authkey = str(uuid.uuid4())
        self._log.debug("Launching Super Vdsm")
        superVdsmCmd = [constants.EXT_PYTHON, SUPERVDSM,
                        self._authkey, str(os.getpid())]
        misc.execCmd(superVdsmCmd, sync=False)
        sleep(2)

    def _killSupervdsm(self):
        try:
            with open(PIDFILE, "r") as f:
                pid = int(f.read().strip())
            misc.execCmd([constants.EXT_KILL, "-9", str(pid)])
        except Exception, ex:
            self._log.debug("Could not kill old Super Vdsm %s", ex)

        self._authkey = None
        self._manager = None

    def _connect(self):
        self._manager = _SuperVdsmManager(address=ADDRESS, authkey=self._authkey)
        self._manager.register('instance')
        self._manager.register('open')
        self._log.debug("Trying to connect to Super Vdsm")
        try:
            self._manager.connect()
        except Exception, ex:
            self._log.debug("Connect failed %s", ex)
            raise
        self._svdsm = self._manager.instance()


    def _restartSupervdsm(self):
        self._killSupervdsm()
        self._launchSupervdsm()
        misc.retry(self._connect, Exception, timeout=60)

    def __getattr__(self, name):
        return ProxyCaller(self, name)

def getProxy():
    global _g_singletonSupervdsmInstance
    if _g_singletonSupervdsmInstance is None:
        with _g_singletonSupervdsmInstance_lock:
            if _g_singletonSupervdsmInstance is None:
                _g_singletonSupervdsmInstance = SuperVdsmProxy()
    return _g_singletonSupervdsmInstance
