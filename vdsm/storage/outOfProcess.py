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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#

import os as mod_os
import glob as mod_glob
import types
from vdsm.config import config
import threading

from fileUtils import open_ex
import fileUtils as mod_fileUtils

from processPool import ProcessPool, ProcessPoolMultiplexer

MAX_HELPERS = config.getint("irs", "process_pool_size")
GRACE_PERIOD = config.getint("irs", "process_pool_grace_period")
DEFAULT_TIMEOUT = config.getint("irs", "process_pool_timeout")
HELPERS_PER_DOMAIN = config.getint("irs", "process_pool_max_slots_per_domain")

_multiplexerLock = threading.Lock()
_multiplexer = [None]


def getProcessPool(clientName):
    if _multiplexer[0] is None:
        with _multiplexerLock:
            if _multiplexer[0] is None:
                pool = ProcessPool(MAX_HELPERS, GRACE_PERIOD, DEFAULT_TIMEOUT)
                _multiplexer[0] = ProcessPoolMultiplexer(pool,
                                                         HELPERS_PER_DOMAIN)

    return OopWrapper(_multiplexer[0][clientName])


def getGlobalProcPool():
    return getProcessPool("Global")


def _simpleWalk(top, topdown=True, onerror=None, followlinks=False):
    # We need this _simpleWalk wrapper because of regular os.walk return
    #iterator and we can't use it in oop.
    filesList = []
    for base, dirs, files in mod_os.walk(top, topdown, onerror, followlinks):
        for f in files:
            filesList.append(mod_os.path.join(base, f))
    return filesList


def _directReadLines(path):
    with open_ex(path, "dr") as f:
        return f.readlines()


def _directWriteLines(path, lines):
    with open_ex(path, "dw") as f:
        return f.writelines(lines)


def _createSparseFile(path, size, mode=None):
    with open(path, "w") as f:
        if mode is not None:
            mod_os.chmod(path, mode)
        f.truncate(size)


def _readLines(path):
    with open(path, "r") as f:
        return f.readlines()


def _writeLines(path, lines):
    with open(path, "w") as f:
        return f.writelines(lines)


class _ModuleWrapper(types.ModuleType):
    def __init__(self, wrappedModule, procPool):
        self._wrappedModule = wrappedModule
        self._procPool = procPool

    def __getattr__(self, name):
        return self._procPool.wrapFunction(getattr(self._wrappedModule, name))


class OopWrapper(object):

    def __init__(self, procPool):
        self._processPool = procPool
        self._registerFunctions()
        self._registerModules()

    def _registerFunctions(self):
        self.simpleWalk = self._processPool.wrapFunction(_simpleWalk)
        self.directReadLines = (self._processPool
                                    .wrapFunction(_directReadLines))
        self.directWriteLines = (self._processPool
                                    .wrapFunction(_directWriteLines))
        self.createSparseFile = (self._processPool
                                    .wrapFunction(_createSparseFile))
        self.readLines = self._processPool.wrapFunction(_readLines)
        self.writeLines = self._processPool.wrapFunction(_writeLines)

    def _registerModules(self):
        self.glob = _ModuleWrapper(mod_glob, self._processPool)
        self.fileUtils = _ModuleWrapper(mod_fileUtils, self._processPool)
        self.os = _ModuleWrapper(mod_os, self._processPool)
        setattr(self.os, 'path',
                _ModuleWrapper(mod_os.path, self._processPool))
