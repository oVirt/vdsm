#
# Copyright 2011-2014 Red Hat, Inc.
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
import errno
import logging
import os
import stat
import sys
import types

from vdsm.config import config
import threading
from functools import partial

try:
    from ioprocess import IOProcess
except ImportError:
    pass

from remoteFileHandler import RemoteFileHandlerPool

RFH = 'rfh'
IOPROC = 'ioprocess'
GLOBAL = 'Global'

_oopImpl = RFH

DEFAULT_TIMEOUT = config.getint("irs", "process_pool_timeout")
HELPERS_PER_DOMAIN = config.getint("irs", "process_pool_max_slots_per_domain")

_procLock = threading.Lock()
_proc = {}


def setDefaultImpl(impl):
    global _oopImpl
    _oopImpl = impl
    if impl == IOPROC and IOPROC not in sys.modules:
        log.warning("Cannot import IOProcess, set oop to use RFH")
        _oopImpl = RFH


def getProcessPool(clientName):
    try:
        return _proc[clientName]
    except KeyError:
        with _procLock:
            if _oopImpl == IOPROC:
                if GLOBAL not in _proc:
                    _proc[GLOBAL] = _OopWrapper(IOProcess(DEFAULT_TIMEOUT))
                _proc[clientName] = _proc[GLOBAL]
            else:
                _proc[clientName] = _OopWrapper(
                    RemoteFileHandlerPool(HELPERS_PER_DOMAIN))

            return _proc[clientName]


def getGlobalProcPool():
    return getProcessPool(GLOBAL)


class _ModuleWrapper(types.ModuleType):
    def __init__(self, modName, procPool, ioproc, timeout, subModNames=()):
        '''
        ioproc : when initialized will override some of RFH functionality
        '''
        self._modName = modName
        self._procPool = procPool
        self._timeout = timeout

        for subModName in subModNames:
            subSubModNames = []
            if isinstance(subModName, tuple):
                subModName, subSubModNames = subModName

            fullModName = "%s.%s" % (modName, subModName)

            setattr(self, subModName,
                    _ModuleWrapper(fullModName,
                                   self._procPool,
                                   ioproc,
                                   DEFAULT_TIMEOUT,
                                   subSubModNames)
                    )

    def __getattr__(self, name):
        # Root modules is fake, we need to remove it
        fullName = ".".join(self._modName.split(".")[1:] + [name])

        return partial(self._procPool.callCrabRPCFunction, self._timeout,
                       fullName)


def _OopWrapper(procPool, ioproc=None):
    return _ModuleWrapper("oop", procPool, ioproc, DEFAULT_TIMEOUT,
                          (("os",
                            ("path",)),
                           "glob",
                           "fileUtils",
                           "utils"))
