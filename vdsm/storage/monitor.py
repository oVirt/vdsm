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

import logging
import threading
import time

from vdsm import concurrent
from vdsm import utils
from vdsm.config import config
from vdsm.storage import clusterlock
from vdsm.storage import misc

from .sdc import sdCache

log = logging.getLogger('Storage.Monitor')


class Status(object):
    __slots__ = (
        "error", "checkTime", "readDelay", "masterMounted",
        "masterValid", "diskUtilization", "vgMdUtilization",
        "vgMdHasEnoughFreeSpace", "vgMdFreeBelowThreashold", "hasHostId",
        "isoPrefix", "version", "actual",
    )

    def __init__(self, actual=True):
        self.actual = actual
        self.error = None
        self.checkTime = time.time()
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
        # The iso prefix is computed asynchronously because in any
        # synchronous operation (e.g.: connectStoragePool, getInfo)
        # we cannot risk to stop and wait for the iso domain to
        # report its prefix (it might be unreachable).
        self.isoPrefix = None
        self.version = -1

    @property
    def valid(self):
        return self.error is None


class FrozenStatus(Status):

    def __init__(self, other):
        for name in other.__slots__:
            value = getattr(other, name)
            super(FrozenStatus, self).__setattr__(name, value)

    def __setattr__(self, *args):
        raise AssertionError('%s is readonly' % self)

    __delattr__ = __setattr__


class DomainMonitor(object):

    def __init__(self, interval):
        self._monitors = {}
        self._interval = interval
        self.onDomainStateChange = misc.Event(
            "Storage.DomainMonitor.onDomainStateChange")

    @property
    def domains(self):
        return self._monitors.keys()

    @property
    def poolDomains(self):
        return [sdUUID for sdUUID, monitor in self._monitors.items()
                if monitor.poolDomain]

    def startMonitoring(self, sdUUID, hostId, poolDomain=True):
        monitor = self._monitors.get(sdUUID)

        # TODO: Replace with explicit attach.
        if monitor is not None:
            if not poolDomain:
                # Expected when hosted engine agent is restarting.
                log.debug("Monitor for %s is already running", sdUUID)
                return

            if monitor.poolDomain:
                log.warning("Monitor for %s is already attached to pool",
                            sdUUID)
                return

            # An external storage domain attached to the pool. From this point,
            # the storage domain is managed by Vdsm.  Expected during Vdsm
            # startup when using hosted engine.
            log.info("Attaching monitor for %s to the pool", sdUUID)
            monitor.poolDomain = True
            return

        log.info("Start monitoring %s", sdUUID)
        monitor = MonitorThread(sdUUID, hostId, self._interval,
                                self.onDomainStateChange)
        monitor.poolDomain = poolDomain
        monitor.start()
        # The domain should be added only after it succesfully started
        self._monitors[sdUUID] = monitor

    def stopMonitoring(self, sdUUIDs):
        sdUUIDs = frozenset(sdUUIDs)
        monitors = [monitor for monitor in self._monitors.values()
                    if monitor.sdUUID in sdUUIDs]
        self._stopMonitors(monitors)

    def isMonitoring(self, sdUUID):
        return sdUUID in self._monitors

    def getDomainsStatus(self):
        for sdUUID, monitor in self._monitors.items():
            yield sdUUID, monitor.getStatus()

    def getHostStatus(self, domains):
        status = {}
        for sdUUID, hostId in domains.iteritems():
            try:
                monitor = self._monitors[sdUUID]
            except KeyError:
                status[sdUUID] = clusterlock.HOST_STATUS_UNAVAILABLE
            else:
                status[sdUUID] = monitor.getHostStatus(hostId)
        return status

    def getHostId(self, sdUUID):
        return self._monitors[sdUUID].hostId

    def shutdown(self):
        """
        Called during shutdown to stop all monitors without releasing the host
        id. To stop monitors and release the host id, use stopMonitoring().
        """
        log.info("Shutting down domain monitors")
        self._stopMonitors(self._monitors.values(), shutdown=True)

    def _stopMonitors(self, monitors, shutdown=False):
        # The domain monitor issues events that might become raceful if
        # you don't wait until a monitor thread exit.
        # Eg: when a domain is detached the domain monitor is stopped and
        # the host id is released. If the monitor didn't actually exit it
        # might respawn a new acquire host id.

        # First stop monitor threads - this take no time, and make the process
        # about 7 times faster when stopping 30 monitors.
        for monitor in monitors:
            log.info("Stop monitoring %s (shutdown=%s)",
                     monitor.sdUUID, shutdown)
            monitor.stop(shutdown=shutdown)

        # Now wait for threads to finish - this takes about 10 seconds with 30
        # monitors, most of the time spent waiting for sanlock.
        for monitor in monitors:
            log.debug("Waiting for monitor %s", monitor.sdUUID)
            monitor.join()
            try:
                del self._monitors[monitor.sdUUID]
            except KeyError:
                log.warning("Montior for %s removed while stopping",
                            monitor.sdUUID)


class MonitorThread(object):

    def __init__(self, sdUUID, hostId, interval, changeEvent):
        self.thread = concurrent.thread(self._run, logger=log.name)
        self.stopEvent = threading.Event()
        self.domain = None
        self.sdUUID = sdUUID
        self.hostId = hostId
        self.interval = interval
        self.changeEvent = changeEvent
        self.monitoringPath = None
        # For backward compatibility, we must present a fake status before
        # collecting the first sample. The fake status is marked as
        # actual=False so engine can handle it correctly.
        self.status = FrozenStatus(Status(actual=False))
        self.isIsoDomain = None
        self.isoPrefix = None
        self.lastRefresh = time.time()
        # Use float to allow short refresh internal during tests.
        self.refreshTime = \
            config.getfloat("irs", "repo_stats_cache_refresh_timeout")
        self.wasShutdown = False
        # Used for synchronizing during the tests
        self.cycleCallback = None

    def start(self):
        self.thread.start()

    def stop(self, shutdown=False):
        self.wasShutdown = shutdown
        self.stopEvent.set()

    def join(self):
        self.thread.join()

    def getStatus(self):
        return self.status

    def getHostStatus(self, hostId):
        if not self.domain:
            return clusterlock.HOST_STATUS_UNAVAILABLE
        return self.domain.getHostStatus(hostId)

    def __canceled__(self):
        """ Accessed by methods decorated with @util.cancelpoint """
        return self.stopEvent.is_set()

    def _run(self):
        log.debug("Domain monitor for %s started", self.sdUUID)
        try:
            self._setupLoop()
            self._monitorLoop()
        except utils.Canceled:
            log.debug("Domain monitor for %s canceled", self.sdUUID)
        finally:
            log.debug("Domain monitor for %s stopped (shutdown=%s)",
                      self.sdUUID, self.wasShutdown)
            if self._shouldReleaseHostId():
                self._releaseHostId()

    # Setting up

    def _setupLoop(self):
        """
        Set up the monitor, retrying on failures. Returns when the monitor is
        ready.
        """
        while True:
            try:
                self._setupMonitor()
                return
            except Exception as e:
                log.exception("Setting up monitor for %s failed", self.sdUUID)
                status = Status()
                status.error = e
                self._updateStatus(status)
                if self.cycleCallback:
                    self.cycleCallback()
                if self.stopEvent.wait(self.interval):
                    raise utils.Canceled

    def _setupMonitor(self):
        # Pick up changes in the domain, for example, domain upgrade.
        if self._shouldRefreshDomain():
            self._refreshDomain()

        # Producing the domain is deferred because it might take some time and
        # we don't want to slow down the thread start (and anything else that
        # relies on that as for example updateMonitoringThreads). It also might
        # fail and we want keep trying until we succeed or the domain is
        # deactivated.
        if self.domain is None:
            self._produceDomain()

        # This may fail even if the domain was produced. We will try again in
        # the next cycle.
        if self.monitoringPath is None:
            self.monitoringPath = self.domain.getMonitoringPath()

        # The isIsoDomain assignment is deferred because the isoPrefix
        # discovery might fail (if the domain suddenly disappears) and we
        # could risk to never try to set it again.
        if self.isIsoDomain is None:
            self._setIsoDomainInfo()

    @utils.cancelpoint
    def _produceDomain(self):
        log.debug("Producing domain %s", self.sdUUID)
        self.domain = sdCache.produce(self.sdUUID)

    @utils.cancelpoint
    def _setIsoDomainInfo(self):
        isIsoDomain = self.domain.isISO()
        if isIsoDomain:
            log.debug("Domain %s is an ISO domain", self.sdUUID)
            self.isoPrefix = self.domain.getIsoDomainImagesDir()
        self.isIsoDomain = isIsoDomain

    # Monitoring

    def _monitorLoop(self):
        """
        Monitor the domain peroidically until the monitor is stopped.
        """
        while True:
            try:
                self._monitorDomain()
            except Exception:
                log.exception("Domain monitor for %s failed", self.sdUUID)
            finally:
                if self.cycleCallback:
                    self.cycleCallback()
            if self.stopEvent.wait(self.interval):
                raise utils.Canceled

    def _monitorDomain(self):
        status = Status()

        # Pick up changes in the domain, for example, domain upgrade.
        if self._shouldRefreshDomain():
            self._refreshDomain()

        try:
            self._performDomainSelftest()
            self._checkReadDelay(status)
            self._collectStatistics(status)
        except Exception as e:
            log.exception("Error monitoring domain %s", self.sdUUID)
            status.error = e

        status.checkTime = time.time()
        self._updateStatus(status)

        if self._shouldAcquireHostId(status):
            self._acquireHostId()

    # Handling status changes

    def _updateStatus(self, status):
        if self._statusDidChange(status):
            self._notifyStatusChanges(status)
        self.status = FrozenStatus(status)

    def _statusDidChange(self, status):
        return (not self.status.actual or
                self.status.valid != status.valid)

    @utils.cancelpoint
    def _notifyStatusChanges(self, status):
        log.info("Domain %s became %s", self.sdUUID,
                 "VALID" if status.valid else "INVALID")
        try:
            self.changeEvent.emit(self.sdUUID, status.valid)
        except:
            log.exception("Error notifying state change for domain %s",
                          self.sdUUID)

    # Refreshing domain

    def _shouldRefreshDomain(self):
        return time.time() - self.lastRefresh > self.refreshTime

    @utils.cancelpoint
    def _refreshDomain(self):
        log.debug("Refreshing domain %s", self.sdUUID)
        sdCache.manuallyRemoveDomain(self.sdUUID)
        self.lastRefresh = time.time()

    # Collecting monitoring info

    @utils.cancelpoint
    def _performDomainSelftest(self):
        # This may trigger a refresh of lvm cache. We have seen this taking up
        # to 90 seconds on overloaded machines.
        self.domain.selftest()

    @utils.cancelpoint
    def _checkReadDelay(self, status):
        # This may block for long time if the storage server is not accessible.
        # On overloaded machines we have seen this take up to 15 seconds.
        stats = misc.readspeed(self.monitoringPath, 4096)
        status.readDelay = stats['seconds']

    def _collectStatistics(self, status):
        stats = self.domain.getStats()
        status.diskUtilization = (stats["disktotal"], stats["diskfree"])

        status.vgMdUtilization = (stats["mdasize"], stats["mdafree"])
        status.vgMdHasEnoughFreeSpace = stats["mdavalid"]
        status.vgMdFreeBelowThreashold = stats["mdathreshold"]

        masterStats = self.domain.validateMaster()
        status.masterValid = masterStats['valid']
        status.masterMounted = masterStats['mount']

        status.hasHostId = self.domain.hasHostId(self.hostId)
        status.isoPrefix = self.isoPrefix
        status.version = self.domain.getVersion()

    # Managing host id

    def _shouldAcquireHostId(self, status):
        # An ISO domain can be shared by multiple pools
        return (not self.isIsoDomain and
                status.valid and
                status.hasHostId is False)

    def _shouldReleaseHostId(self):
        # If this is an ISO domain we didn't acquire the host id and releasing
        # it is superfluous.
        # During shutdown we do not release the host id, in case there is a vm
        # holding a resource on this domain, such as the hosted engine vm.
        return self.domain and not self.isIsoDomain and not self.wasShutdown

    @utils.cancelpoint
    def _acquireHostId(self):
        try:
            self.domain.acquireHostId(self.hostId, async=True)
        except:
            log.exception("Error acquiring host id %s for domain %s",
                          self.hostId, self.sdUUID)

    def _releaseHostId(self):
        try:
            self.domain.releaseHostId(self.hostId, unused=True)
        except:
            log.exception("Error releasing host id %s for domain %s",
                          self.hostId, self.sdUUID)
