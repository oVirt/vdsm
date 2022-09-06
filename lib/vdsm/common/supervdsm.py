# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

import os
from multiprocessing.managers import BaseManager, RemoteError
import logging
import threading

from vdsm.common import constants
from vdsm.common import function
from vdsm.common.panic import panic

_g_singletonSupervdsmInstance = None
_g_singletonSupervdsmInstance_lock = threading.Lock()


ADDRESS = os.path.join(constants.P_VDSM_RUN, "svdsm.sock")


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
        try:
            return callMethod()
        except RemoteError:
            self._supervdsmProxy._connect()
            raise RuntimeError(
                "Broken communication with supervdsm. Failed call to %s"
                % self._funcName)


class SuperVdsmProxy(object):
    """
    A wrapper around all the supervdsm init stuff
    """
    _log = logging.getLogger("SuperVdsmProxy")

    def __init__(self):
        self._manager = None
        self._svdsm = None
        self._connect()

    def open(self, *args, **kwargs):
        # pylint: disable=no-member
        return self._manager.open(*args, **kwargs)

    def _connect(self):
        self._manager = _SuperVdsmManager(address=ADDRESS, authkey=b'')
        self._manager.register('instance')
        self._manager.register('open')
        self._log.debug("Trying to connect to Super Vdsm")
        try:
            function.retry(
                self._manager.connect, Exception, timeout=60, tries=3)
        except Exception as ex:
            msg = "Connect to supervdsm service failed: %s" % ex
            panic(msg)

        # pylint: disable=no-member
        self._svdsm = self._manager.instance()

    def __getattr__(self, name):
        return ProxyCaller(self, name)


def getProxy():
    global _g_singletonSupervdsmInstance
    if _g_singletonSupervdsmInstance is None:
        with _g_singletonSupervdsmInstance_lock:
            if _g_singletonSupervdsmInstance is None:
                _g_singletonSupervdsmInstance = SuperVdsmProxy()
    return _g_singletonSupervdsmInstance
