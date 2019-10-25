#
# Copyright 2009-2017 Red Hat, Inc.
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
from __future__ import absolute_import

import logging
import threading

from vdsm import utils
from vdsm.storage import exception as se
from vdsm.storage import lvm
from vdsm.storage import misc
from vdsm.storage import multipath


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

    log = logging.getLogger('storage.StorageDomainCache')

    STORAGE_UPDATED = 0
    STORAGE_STALE = 1
    STORAGE_REFRESHING = 2

    def __init__(self):
        self._syncroot = threading.Condition()
        self.__domainCache = {}
        self.__inProgress = set()
        self.__staleStatus = self.STORAGE_STALE
        self.knownSDs = {}  # {sdUUID: mod.findDomain}

    def invalidateStorage(self):
        self.log.info("Invalidating storage domain cache")
        with self._syncroot:
            self.__staleStatus = self.STORAGE_STALE

    @misc.samplingmethod
    def refreshStorage(self, resize=True):
        self.log.info("Refreshing storage domain cache (resize=%s)", resize)
        with utils.stopwatch(
                "Refreshing storage domain cache",
                level=logging.INFO,
                log=self.log):
            self.__staleStatus = self.STORAGE_REFRESHING

            multipath.rescan()
            if resize:
                multipath.resize_devices()
            lvm.invalidateCache()

            # If a new invalidateStorage request came in after the refresh
            # started then we cannot flag the storages as updated (force a
            # new rescan later).
            with self._syncroot:
                if self.__staleStatus == self.STORAGE_REFRESHING:
                    self.__staleStatus = self.STORAGE_UPDATED

    def produce_manifest(self, sdUUID):
        """
        Return a StorageDomainManifest for sdUUID. New code must use this, as
        StorgeDomain is not safe for use in spm-less code.
        """
        return self.produce(sdUUID).manifest

    def produce(self, sdUUID):
        """
        Return a StorageDomain for sdUUID. This must be used only in legacy
        code.
        """
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
            findMethod = self._findUnfetchedDomain

        return findMethod(sdUUID)

    def _findUnfetchedDomain(self, sdUUID):
        from vdsm.storage import blockSD
        from vdsm.storage import glusterSD
        from vdsm.storage import localFsSD
        from vdsm.storage import nfsSD

        # The order is somewhat important, it's ordered
        # by how quickly get can find the domain. For instance
        # if an nfs mount is unavailable we will get stuck
        # until it times out, this should affect fetching
        # of block\local domains. If for any case in the future
        # this changes, please update the order.

        self.log.info("Looking up domain %s", sdUUID)
        with utils.stopwatch(
                "Looking up domain {}".format(sdUUID),
                level=logging.INFO,
                log=self.log):
            for mod in (blockSD, glusterSD, localFsSD, nfsSD):
                try:
                    return mod.findDomain(sdUUID)
                except se.StorageDomainDoesNotExist:
                    pass
                except Exception:
                    self.log.error(
                        "Error while looking for domain `%s`",
                        sdUUID, exc_info=True)

        raise se.StorageDomainDoesNotExist(sdUUID)

    def getUUIDs(self):
        from vdsm.storage import blockSD
        from vdsm.storage import fileSD

        uuids = []
        for mod in (blockSD, fileSD):
            uuids.extend(mod.getStorageDomainsList())

        return uuids

    def refresh(self):
        self.log.info("Clearing storage domain cache")
        with self._syncroot:
            lvm.invalidateCache()
            self.__domainCache.clear()

    def manuallyAddDomain(self, domain):
        self.log.info(
            "Adding domain %s to storage domain cache", domain.sdUUID)
        with self._syncroot:
            self.__domainCache[domain.sdUUID] = domain

    def manuallyRemoveDomain(self, sdUUID):
        self.log.info("Removing domain %s from storage domain cache", sdUUID)
        with self._syncroot:
            try:
                del self.__domainCache[sdUUID]
            except KeyError:
                pass


sdCache = StorageDomainCache()
