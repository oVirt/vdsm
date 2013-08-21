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
import types

from vdsm.config import config
import threading
from functools import partial

from remoteFileHandler import RemoteFileHandlerPool

# MAX_HELPERS = config.getint("irs", "process_pool_size")
# GRACE_PERIOD = config.getint("irs", "process_pool_grace_period")
DEFAULT_TIMEOUT = config.getint("irs", "process_pool_timeout")
HELPERS_PER_DOMAIN = config.getint("irs", "process_pool_max_slots_per_domain")

_poolsLock = threading.Lock()
_pools = {}


def getProcessPool(clientName):
    try:
        return _pools[clientName]
    except KeyError:
        with _poolsLock:
            if not clientName in _pools:
                _pools[clientName] = OopWrapper(
                    RemoteFileHandlerPool(HELPERS_PER_DOMAIN))

            return _pools[clientName]


def getGlobalProcPool():
    return getProcessPool("Global")


class _ModuleWrapper(types.ModuleType):
    def __init__(self, modName, procPool, timeout, subModNames=()):
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
                                   DEFAULT_TIMEOUT,
                                   subSubModNames)
                    )

    def __getattr__(self, name):
        # Root modules is fake, we need to remove it
        fullName = ".".join(self._modName.split(".")[1:] + [name])

        return partial(self._procPool.callCrabRPCFunction, self._timeout,
                       fullName)


def OopWrapper(procPool):
    return _ModuleWrapper("oop", procPool, DEFAULT_TIMEOUT,
                          (("os",
                            ("path",)),
                           "glob",
                           "fileUtils",
                           "utils"))
