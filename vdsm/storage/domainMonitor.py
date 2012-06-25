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

from threading import Thread, Event
from time import time
import logging
import misc


class DomainMonitorStatus(object):
    __slots__ = (
        "error", "lastCheck", "valid", "readDelay", "masterMounted",
        "masterValid", "diskUtilization", "vgMdUtilization",
        "vgMdHasEnoughFreeSpace", "vgMdFreeBelowThreashold", "hasHostId",
    )

    def __init__(self):
        self.clear()

    def clear(self):
        self.error = None
        self.lastCheck = time()
        self.valid = True
        self.readDelay = 0
        self.diskUtilization = (None, None)
        self.masterMounted = False
        self.masterValid = False
        self.hasHostId = False
        # FIXME : Exposing these breaks abstraction and is not
        #         needed. Keep exposing for BC. Remove and use
        #         warning mechanism.
        self.vgMdUtilization = (0, 0)
        self.vgMdHasEnoughFreeSpace = True
        self.vgMdFreeBelowThreashold = True

    def update(self, st):
        for attr in self.__slots__:
            setattr(self, attr, getattr(st, attr))

    def copy(self):
        res = DomainMonitorStatus()
        res.update(self)
        return res


class DomainMonitor(object):
    log = logging.getLogger('Storage.DomainMonitor')

    def __init__(self, interval):
        self._domains = {}
        self._interval = interval
        self.onDomainConnectivityStateChange = misc.Event(
            "Storage.DomainMonitor.onDomainConnectivityStateChange")

    @property
    def monitoredDomains(self):
        return self._domains.keys()

    def startMonitoring(self, domain, hostId):
        if domain.sdUUID in self._domains:
            return

        status = DomainMonitorStatus()
        stopEvent = Event()
        thread = Thread(target=self._monitorDomain,
                        args=(domain, hostId, stopEvent, status))

        thread.setDaemon(True)
        thread.start()
        self._domains[domain.sdUUID] = (stopEvent, thread, status)

    def stopMonitoring(self, sdUUID):
        if sdUUID not in self._domains:
            return

        stopEvent, thread = self._domains[sdUUID][:2]
        stopEvent.set()
        # The domain monitor issues events that might become raceful if
        # stopMonitoring doesn't stop until the thread exits.
        # Eg: when a domain is detached the domain monitor is stopped and
        # the host id is released. If the monitor didn't actually exit it
        # might respawn a new acquire host id.
        thread.join()
        del self._domains[sdUUID]

    def getStatus(self, sdUUID):
        status = self._domains[sdUUID][-1]
        return status.copy()

    def close(self):
        for sdUUID in self._domains.keys():
            self.stopMonitoring(sdUUID)

    def _monitorDomain(self, domain, hostId, stopEvent, status):
        nextStatus = DomainMonitorStatus()
        isIsoDomain = domain.isISO()

        while not stopEvent.is_set():
            nextStatus.clear()
            try:
                domain.selftest()

                nextStatus.readDelay = domain.getReadDelay()

                stats = domain.getStats()
                nextStatus.diskUtilization = (stats["disktotal"],
                                              stats["diskfree"])

                nextStatus.vgMdUtilization = (stats["mdasize"],
                                              stats["mdafree"])

                nextStatus.vgMdHasEnoughFreeSpace = stats["mdavalid"]
                nextStatus.vgMdFreeBelowThreashold = stats["mdathreshold"]

                masterStats = domain.validateMaster()
                nextStatus.masterValid = masterStats['valid']
                nextStatus.masterMounted = masterStats['mount']

                nextStatus.hasHostId = domain.hasHostId(hostId)

            except Exception, e:
                self.log.error("Error while collecting domain `%s` monitoring "
                        "information", domain.sdUUID, exc_info=True)
                nextStatus.error = e

            nextStatus.lastCheck = time()
            nextStatus.valid = (nextStatus.error is None)

            if status.valid != nextStatus.valid:
                self.log.debug("Domain `%s` changed its status to %s",
                    domain.sdUUID, "Valid" if nextStatus.valid else "Invalid")

                try:
                    self.onDomainConnectivityStateChange.emit(domain.sdUUID,
                                                              nextStatus.valid)
                except:
                    self.log.warn("Could not emit domain state change event",
                                  exc_info=True)

            # An ISO domain can be shared by multiple pools
            if (not isIsoDomain
                    and nextStatus.valid and nextStatus.hasHostId is False):
                try:
                    domain.acquireHostId(hostId, async=True)
                except:
                    self.log.debug("Unable to issue the acquire host id %s "
                            "request for the domain %s", hostId, domain.sdUUID,
                            exc_info=True)

            status.update(nextStatus)
            stopEvent.wait(self._interval)

        self.log.debug("Monitorg for domain %s is stopping", domain.sdUUID)

        try:
            domain.releaseHostId(hostId, unused=True)
        except:
            self.log.debug("Unable to release the host id %s for the domain "
                           "%s",  hostId, domain.sdUUID, exc_info=True)
