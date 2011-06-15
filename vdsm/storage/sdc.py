#
# Copyright 2009 Red Hat, Inc. and/or its affiliates.
#
# Licensed to you under the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.  See the files README and
# LICENSE_GPL_v2 which accompany this distribution.
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

    @misc.samplingmethod
    def refreshStorage(self):
        multipath.rescan()
        lvm.updateLvmConf()
        self.storageStale = False
        self.invalidate()

    def invalidate(self):
        """
        """
        # TODO : Remove all calls to this
        pass

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
