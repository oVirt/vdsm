#
# Copyright 2009-2011 Red Hat, Inc.
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

"""
Cache module provides general purpose (more or less) cache infrastructure
for keeping storage related data that is expensive to harvest, but needed often
"""
import logging
import threading
import weakref

import multipath
import lvm
import misc
import storage_exception as se

# Default cache age until forcibly refreshed
DEFAULT_REFRESH_INTERVAL = 300

class StorageDomainCache:
    """
    Storage Domain List keeps track of all the storage domains accessible by the
    current system.
    """

    log = logging.getLogger('Storage.StorageDomainCache')
    def __init__(self, storage_repo):
        self._syncroot = threading.Lock()
        self.__cache = {}
        self.__weakCache = {}
        self.storage_repo = storage_repo
        self.storageStale = True


    def invalidateStorage(self):
        self.storageStale = True
        lvm.invalidateCache()

    @misc.samplingmethod
    def refreshStorage(self):
        multipath.rescan()
        lvm.invalidateCache()
        self.storageStale = False

    def _getDomainFromCache(self, sdUUID):
        try:
            return self.__weakCache[sdUUID]()
        except KeyError:
            return None

    def _cleanStaleWeakrefs(self):
        for sdUUID, ref in self.__weakCache.items():
            if ref() is None:
                del self.__weakCache[sdUUID]


    def lookup(self, sdUUID):
        dom = self._getDomainFromCache(sdUUID)
        if dom:
            return dom

        with self._syncroot:
            dom = self._getDomainFromCache(sdUUID)
            if dom:
                return dom

            if self.storageStale:
                self.refreshStorage()

            self._cleanStaleWeakrefs()

            #_findDomain will raise StorageDomainDoesNotExist if sdUUID is not found in storage.
            dom = self._findDomain(sdUUID)
            self.__cache[sdUUID] = dom
            self.__weakCache[sdUUID] = weakref.ref(dom)
            return dom

    def _findDomain(self, sdUUID):
        import blockSD
        import localFsSD
        import nfsSD

        # The order is somewhat important, it's ordered
        # by how quickly get can find the domain. For instance
        # if an nfs mount is unavailable we will get stuck
        # until it times out, this should affect fetching
        # of block\local domains. If for any case in the future
        # this changes, please update the order.
        for mod in (blockSD, localFsSD, nfsSD):
            try:
                return mod.findDomain(sdUUID)
            except se.StorageDomainDoesNotExist:
                pass
            except Exception:
                self.log.error("Error while looking for domain `%s`", sdUUID, exc_info=True)

        raise se.StorageDomainDoesNotExist(sdUUID)

    def getUUIDs(self):
        import blockSD
        import fileSD

        uuids = []
        for mod in (blockSD, fileSD):
            uuids.extend(mod.getStorageDomainsList())

        return uuids

    def refresh(self):
        self.invalidateStorage()
        self.__cache.clear()

    def manuallyAddDomain(self, dom):
        with self._syncroot:
            self.__cache[dom.sdUUID] = dom

    def manuallyRemoveDomain(self, sdUUID):
        with self._syncroot:
            del self.__cache[sdUUID]
