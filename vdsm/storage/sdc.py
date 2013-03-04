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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#

"""
Cache module provides general purpose (more or less) cache infrastructure
for keeping storage related data that is expensive to harvest, but needed often
"""
import logging
import threading
from vdsm.config import config

import multipath
import lvm
import misc
import storage_exception as se

# Default cache age until forcibly refreshed
DEFAULT_REFRESH_INTERVAL = 300


class DomainProxy(object):
    """
    Keeps domain references valid even when underlying domain object changes
    (due to format conversion for example).
    """

    def __init__(self, cache, sdUUID):
        self._sdUUID = sdUUID
        self._cache = cache

    def __getattr__(self, attrName):
        return getattr(self.getRealDomain(), attrName)

    def getRealDomain(self):
        return self._cache._realProduce(self._sdUUID)


class StorageDomainCache:
    """
    Storage Domain List keeps track of all the storage domains accessible by
    the current system.
    """

    log = logging.getLogger('Storage.StorageDomainCache')

    STORAGE_UPDATED = 0
    STORAGE_STALE = 1
    STORAGE_REFRESHING = 2

    def __init__(self, storage_repo):
        self._syncroot = threading.Condition()
        self.__domainCache = {}
        self.__inProgress = set()
        self.__staleStatus = self.STORAGE_STALE
        self.storage_repo = storage_repo
        self.knownSDs = {}  # {sdUUID: mod.findDomain}

    def invalidateStorage(self):
        with self._syncroot:
            self.__staleStatus = self.STORAGE_STALE

    @misc.samplingmethod
    def refreshStorage(self):
        self.__staleStatus = self.STORAGE_REFRESHING

        multipath.rescan()
        lvm.invalidateCache()

        # If a new invalidateStorage request came in after the refresh
        # started then we cannot flag the storages as updated (force a
        # new rescan later).
        with self._syncroot:
            if self.__staleStatus == self.STORAGE_REFRESHING:
                self.__staleStatus = self.STORAGE_UPDATED

    def produce(self, sdUUID):
        domain = DomainProxy(self, sdUUID)
        # This is needed to preserve the semantic where if the domain
        # was absent from the cache and the domain cannot be found the
        # operation would fail.
        domain.getRealDomain()
        return domain

    def _realProduce(self, sdUUID):
        with self._syncroot:
            while True:
                domain = self.__domainCache.get(sdUUID)

                if domain is not None:
                    return domain

                if sdUUID not in self.__inProgress:
                    self.__inProgress.add(sdUUID)
                    break

                self._syncroot.wait()

        try:
            # If multiple calls reach this point and the storage is not
            # updated the refreshStorage() sampling method is called
            # serializing (and eventually grouping) the requests.
            if self.__staleStatus != self.STORAGE_UPDATED:
                self.refreshStorage()

            domain = self._findDomain(sdUUID)

            with self._syncroot:
                self.__domainCache[sdUUID] = domain
                return domain

        finally:
            with self._syncroot:
                self.__inProgress.remove(sdUUID)
                self._syncroot.notifyAll()

    def _findDomain(self, sdUUID):
        try:
            findMethod = self.knownSDs[sdUUID]
        except KeyError:
            self.log.error("looking for unfetched domain %s", sdUUID)
            findMethod = self._findUnfetchedDomain

        try:
            dom = findMethod(sdUUID)
        except se.StorageDomainDoesNotExist:
            self.log.error("domain %s not found", sdUUID, exc_info=True)
            raise
        else:
            return dom

    def _findUnfetchedDomain(self, sdUUID):
        import blockSD
        import glusterSD
        import localFsSD
        import nfsSD

        self.log.error("looking for domain %s", sdUUID)

        # The order is somewhat important, it's ordered
        # by how quickly get can find the domain. For instance
        # if an nfs mount is unavailable we will get stuck
        # until it times out, this should affect fetching
        # of block\local domains. If for any case in the future
        # this changes, please update the order.
        for mod in (blockSD, glusterSD, localFsSD, nfsSD):
            try:
                return mod.findDomain(sdUUID)
            except se.StorageDomainDoesNotExist:
                pass
            except Exception:
                self.log.error("Error while looking for domain `%s`", sdUUID,
                               exc_info=True)

        raise se.StorageDomainDoesNotExist(sdUUID)

    def getUUIDs(self):
        import blockSD
        import fileSD

        uuids = []
        for mod in (blockSD, fileSD):
            uuids.extend(mod.getStorageDomainsList())

        return uuids

    def refresh(self):
        with self._syncroot:
            lvm.invalidateCache()
            self.__domainCache.clear()

    def manuallyAddDomain(self, domain):
        with self._syncroot:
            self.__domainCache[domain.sdUUID] = domain

    def manuallyRemoveDomain(self, sdUUID):
        with self._syncroot:
            try:
                del self.__domainCache[sdUUID]
            except KeyError:
                pass


storage_repository = config.get('irs', 'repository')
sdCache = StorageDomainCache(storage_repository)
