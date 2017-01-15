#
# Copyright 2011-2017 Red Hat, Inc.
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
from __future__ import absolute_import

import logging
import threading
import time

from vdsm import utils
from vdsm.common import concurrent
from vdsm.config import config
from vdsm.storage import check
from vdsm.storage import clusterlock
from vdsm.storage import misc
from vdsm.storage.sdc import sdCache

log = logging.getLogger('storage.Monitor')


class Status(object):

    def __init__(self, path_status, domain_status):
        self._path_status = path_status
        self._domain_status = domain_status
        self._time = time.time()

    @property
    def actual(self):
        """
        Return True if this status is actual status or the initial status used
        before the first check has completed.

        Note that once we have any parial failed status (e.g. failed
        path_status), the combined status is considered actual. But if we have
        only partial successful status, the combined status is not considered
        not actual, until both path status and domain status are actual.

        This keeps the behvior of the old code, and prevent flipping of the
        status when one status check fails, the other succeeds.
        """
        if not self.valid:
            return True
        return self._path_status.actual and self._domain_status.actual

    @property
    def error(self):
        return self._path_status.error or self._domain_status.error

    @property
    def valid(self):
        return self.error is None

    @property
    def checkTime(self):
        return self._time

    @property
    def readDelay(self):
        return self._path_status.readDelay

    @property
    def diskUtilization(self):
        return self._domain_status.diskUtilization

    @property
    def masterMounted(self):
        return self._domain_status.masterMounted

    @property
    def masterValid(self):
        return self._domain_status.masterValid

    @property
    def hasHostId(self):
        return self._domain_status.hasHostId

    @property
    def vgMdUtilization(self):
        return self._domain_status.vgMdUtilization

    @property
    def vgMdHasEnoughFreeSpace(self):
        return self._domain_status.vgMdHasEnoughFreeSpace

    @property
    def vgMdFreeBelowThreashold(self):
        return self._domain_status.vgMdFreeBelowThreashold

    @property
    def isoPrefix(self):
        return self._domain_status.isoPrefix

    @property
    def version(self):
        return self._domain_status.version


class PathStatus(object):

    def __init__(self, readDelay=0, error=None, actual=True):
        self.readDelay = readDelay
        self.error = error
        self.actual = actual


class DomainStatus(object):

    def __init__(self, error=None, actual=True):
        self.error = error
        self.actual = actual
        self.diskUtilization = (None, None)
        self.masterMounted = False
        self.masterValid = False
        self.hasHostId = False
        self.vgMdUtilization = (0, 0)
        self.vgMdHasEnoughFreeSpace = True
        self.vgMdFreeBelowThreashold = True
        self.isoPrefix = None
        self.version = -1


class DomainMonitor(object):

    def __init__(self, interval):
        self._monitors = {}
        self._interval = interval
        # NOTE: This must be used in asynchronous mode to prevent blocking of
        # the checker event loop thread.
        self.onDomainStateChange = misc.Event(
            "storage.DomainMonitor.onDomainStateChange", sync=False)
        self._checker = check.CheckService()
        self._checker.start()

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
                                self.onDomainStateChange, self._checker)
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
        self._checker.stop()

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

    def __init__(self, sdUUID, hostId, interval, changeEvent, checker):
        self.thread = concurrent.thread(self._run, log=log,
                                        name="monitor/" + sdUUID[:7])
        self.stopEvent = threading.Event()
        self.domain = None
        self.sdUUID = sdUUID
        self.hostId = hostId
        self.interval = interval
        self.changeEvent = changeEvent
        self.checker = checker
        self.lock = threading.Lock()
        self.monitoringPath = None
        # For backward compatibility, we must present a fake status before
        # collecting the first sample. The fake status is marked as
        # actual=False so engine can handle it correctly.
        self.status = Status(PathStatus(actual=False),
                             DomainStatus(actual=False))
        self.isIsoDomain = None
        self.isoPrefix = None
        self.lastRefresh = time.time()
        # Use float to allow short refresh internal during tests.
        self.refreshTime = \
            config.getfloat("irs", "repo_stats_cache_refresh_timeout")
        self.wasShutdown = False
        # Used for synchronizing during the tests
        self.cycleCallback = _NULL_CALLBACK

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
            self._stopCheckingPath()
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
                domain_status = DomainStatus(error=e)
                status = Status(self.status._path_status, domain_status)
                self._updateStatus(status)
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
            self.checker.start_checking(self.monitoringPath, self._pathChecked,
                                        interval=self.interval)

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
                self.cycleCallback()
            if self.stopEvent.wait(self.interval):
                raise utils.Canceled

    def _monitorDomain(self):
        # Pick up changes in the domain, for example, domain upgrade.
        if self._shouldRefreshDomain():
            self._refreshDomain()

        self._checkDomainStatus()

    @utils.cancelpoint
    def _checkDomainStatus(self):
        domain_status = DomainStatus()
        try:
            # This may trigger a refresh of lvm cache. We have seen this taking
            # up to 90 seconds on overloaded machines.
            self.domain.selftest()

            stats = self.domain.getStats()
            domain_status.diskUtilization = (stats["disktotal"],
                                             stats["diskfree"])

            domain_status.vgMdUtilization = (stats["mdasize"],
                                             stats["mdafree"])
            domain_status.vgMdHasEnoughFreeSpace = stats["mdavalid"]
            domain_status.vgMdFreeBelowThreashold = stats["mdathreshold"]

            masterStats = self.domain.validateMaster()
            domain_status.masterValid = masterStats['valid']
            domain_status.masterMounted = masterStats['mount']

            domain_status.hasHostId = self.domain.hasHostId(self.hostId)
            domain_status.version = self.domain.getVersion()
            domain_status.isoPrefix = self.isoPrefix
        except Exception as e:
            log.exception("Error checking domain %s", self.sdUUID)
            domain_status.error = e

        with self.lock:
            status = Status(self.status._path_status, domain_status)
            self._updateStatus(status)

        if self._shouldAcquireHostId():
            self._acquireHostId()

    # Handling status changes

    def _updateStatus(self, status):
        if self._statusDidChange(status):
            self._notifyStatusChanges(status)
        self.status = status

    def _statusDidChange(self, status):
        # Wait until status contains actual data
        if not status.actual:
            return False
        # Is this the first check?
        if not self.status.actual:
            return True
        # Report status changes
        return self.status.valid != status.valid

    @utils.cancelpoint
    def _notifyStatusChanges(self, status):
        log.info("Domain %s became %s", self.sdUUID,
                 "VALID" if status.valid else "INVALID")
        try:
            # NOTE: We depend on this being asynchrounous, so we don't block
            # the checker event loop thread.
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

    # Checking monitoring path

    def _pathChecked(self, result):
        """
        Called from the checker event loop thread. Must not block!
        """
        try:
            delay = result.delay()
        except Exception as e:
            log.exception("Error checking path %s", self.monitoringPath)
            path_status = PathStatus(error=e)
        else:
            path_status = PathStatus(readDelay=delay)

        with self.lock:
            # NOTE: Everyting under this lock must not block for long time, or
            # we will block the checker event loop thread.
            status = Status(path_status, self.status._domain_status)
            self._updateStatus(status)

    def _stopCheckingPath(self):
        """
        Called during monitor shutdown, must not raise!
        """
        if self.monitoringPath is None:
            return
        try:
            # May fail with KeyError if path is not being checked (unlikely).
            self.checker.stop_checking(self.monitoringPath, timeout=1.0)
        except Exception:
            log.exception("Error stopping checker for %s", self.monitoringPath)

    # Managing host id

    def _shouldAcquireHostId(self):
        # An ISO domain can be shared by multiple pools
        if self.isIsoDomain:
            return False

        # Do we have enough data?
        if not self.status.actual:
            return False

        # No point to acquire if storage is not accessible
        if not self.status.valid:
            return False

        # Acquire if not acquired yet
        return self.status.hasHostId is False

    def _shouldReleaseHostId(self):
        # Did we finish setup?
        if not self.domain:
            return False

        # If this is an ISO domain we didn't acquire the host id and releasing
        # it is superfluous.
        if self.isIsoDomain:
            return False

        # During shutdown we do not release the host id, in case there is a vm
        # holding a resource on this domain, such as the hosted engine vm.
        if self.wasShutdown:
            return False

        # Did we have enough data to acquire the host id?
        if not self.status.actual:
            return False

        # It is possible that we tried to acquire the host id. Note that trying
        # to relase before acquiring is safe.
        return True

    @utils.cancelpoint
    def _acquireHostId(self):
        try:
            self.domain.acquireHostId(self.hostId, async=True)
        except:
            log.exception("Error acquiring host id %s for domain %s",
                          self.hostId, self.sdUUID)

    def _releaseHostId(self):
        """
        Called during monitor shutdown, must not raise!
        """
        try:
            self.domain.releaseHostId(self.hostId, unused=True)
        except:
            log.exception("Error releasing host id %s for domain %s",
                          self.hostId, self.sdUUID)


def _NULL_CALLBACK():
    pass
