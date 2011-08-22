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

import os as mod_os
import glob as mod_glob
import types
from config import config

from fileUtils import open_ex
import fileUtils as mod_fileUtils

from processPool import ProcessPool

MAX_HELPERS = config.getint("irs", "process_pool_size")
GRACE_PERIOD = config.getint("irs", "process_pool_grace_period")
DEFAULT_TIMEOUT = config.getint("irs", "process_pool_timeout")

_globalPool = ProcessPool(MAX_HELPERS, GRACE_PERIOD, DEFAULT_TIMEOUT)

def _simpleWalk(top, topdown=True, onerror=None, followlinks=False):
    # We need this _simpleWalk wrapper because of regular os.walk return iterator
    # and we can't use it in oop.
    filesList = []
    for base, dirs, files in mod_os.walk(top, topdown, onerror, followlinks):
        for f in files:
            filesList.append(mod_os.path.join(base,f))
    return filesList
simpleWalk = _globalPool.wrapFunction(_simpleWalk)

def _directReadLines(path):
    with open_ex(path, "dr") as f:
        return f.readlines()
directReadLines = _globalPool.wrapFunction(_directReadLines)

def _directWriteLines(path, lines):
    with open_ex(path, "dw") as f:
        return f.writelines(lines)
directWriteLines = _globalPool.wrapFunction(_directWriteLines)

def _createSparseFile(path, size):
    with open(path, "w") as f:
        f.truncate(size)
createSparseFile = _globalPool.wrapFunction(_createSparseFile)

def _readLines(path):
    with open(path, "r") as f:
        return f.readlines()
readLines = _globalPool.wrapFunction(_readLines)

def _writeLines(path, lines):
    with open(path, "w") as f:
        return f.writelines(lines)
writeLines = _globalPool.wrapFunction(_writeLines)

class _ModuleWrapper(types.ModuleType):
    def __init__(self, wrappedModule, procPool=None):
        self._wrappedModule = wrappedModule
        self._procPool = procPool

    def __getattr__(self, name):
        if self._procPool:
            return self._procPool.wrapFunction(getattr(self._wrappedModule, name))
        else:
            return _globalPool.wrapFunction(getattr(self._wrappedModule, name))

glob = _ModuleWrapper(mod_glob)

os = _ModuleWrapper(mod_os)
setattr(os, 'path', _ModuleWrapper(mod_os.path))

fileUtils = _ModuleWrapper(mod_fileUtils)

class OopWrapper(object):
    def __init__(self, procPool):
        self._processPool = procPool
        self._registerFunctions()
        self._registerModules()

    def _registerFunctions(self):
        self.simpleWalk = self._processPool.wrapFunction(_simpleWalk)
        self.directReadLines = self._processPool.wrapFunction(_directReadLines)
        self.directWriteLines = self._processPool.wrapFunction(_directWriteLines)
        self.createSparseFile = self._processPool.wrapFunction(_createSparseFile)
        self.readLines = self._processPool.wrapFunction(_readLines)
        self.writeLines = self._processPool.wrapFunction(_writeLines)

    def _registerModules(self):
        self.glob = _ModuleWrapper(mod_glob, self._processPool)
        self.fileUtils = _ModuleWrapper(mod_fileUtils, self._processPool)
        self.os = _ModuleWrapper(mod_os, self._processPool)
        setattr(self.os, 'path', _ModuleWrapper(mod_os.path, self._processPool))
