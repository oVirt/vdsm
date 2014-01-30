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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#

import os
import fcntl
import threading
import logging
import subprocess
from contextlib import nested
import sanlock

import misc
import storage_exception as se
from vdsm import constants
from vdsm.config import config
from vdsm import utils


MAX_HOST_ID = 250

# The LEASE_OFFSET is used by SANLock to not overlap with safelease in
# orfer to preserve the ability to acquire both locks (e.g.: during the
# domain upgrade)
SDM_LEASE_NAME = 'SDM'
SDM_LEASE_OFFSET = 512 * 2048


class InquireNotSupportedError(Exception):
    """Raised when the clusterlock class is not supporting inquire"""


class SafeLease(object):
    log = logging.getLogger("Storage.SafeLease")

    lockUtilPath = config.get('irs', 'lock_util_path')
    lockCmd = config.get('irs', 'lock_cmd')
    freeLockCmd = config.get('irs', 'free_lock_cmd')

    def __init__(self, sdUUID, idsPath, leasesPath, lockRenewalIntervalSec,
                 leaseTimeSec, leaseFailRetry, ioOpTimeoutSec):
        self._lock = threading.Lock()
        self._sdUUID = sdUUID
        self._idsPath = idsPath
        self._leasesPath = leasesPath
        self.setParams(lockRenewalIntervalSec, leaseTimeSec, leaseFailRetry,
                       ioOpTimeoutSec)

    def initLock(self):
        lockUtil = os.path.join(self.lockUtilPath, "safelease")
        initCommand = [lockUtil, "release", "-f", self._leasesPath, "0"]
        rc, out, err = misc.execCmd(initCommand, cwd=self.lockUtilPath)
        if rc != 0:
            self.log.warn("could not initialise spm lease (%s): %s", rc, out)
            raise se.ClusterLockInitError()

    def setParams(self, lockRenewalIntervalSec, leaseTimeSec, leaseFailRetry,
                  ioOpTimeoutSec):
        self._lockRenewalIntervalSec = lockRenewalIntervalSec
        self._leaseTimeSec = leaseTimeSec
        self._leaseFailRetry = leaseFailRetry
        self._ioOpTimeoutSec = ioOpTimeoutSec

    def getReservedId(self):
        return 1000

    def acquireHostId(self, hostId, async):
        self.log.debug("Host id for domain %s successfully acquired (id: %s)",
                       self._sdUUID, hostId)

    def releaseHostId(self, hostId, async, unused):
        self.log.debug("Host id for domain %s released successfully (id: %s)",
                       self._sdUUID, hostId)

    def hasHostId(self, hostId):
        return True

    def acquire(self, hostID):
        leaseTimeMs = self._leaseTimeSec * 1000
        ioOpTimeoutMs = self._ioOpTimeoutSec * 1000
        with self._lock:
            self.log.debug("Acquiring cluster lock for domain %s" %
                           self._sdUUID)

            lockUtil = self.getLockUtilFullPath()
            acquireLockCommand = subprocess.list2cmdline([
                lockUtil, "start", self._sdUUID, str(hostID),
                str(self._lockRenewalIntervalSec), str(self._leasesPath),
                str(leaseTimeMs), str(ioOpTimeoutMs), str(self._leaseFailRetry)
            ])

            cmd = [constants.EXT_SU, misc.IOUSER, '-s', constants.EXT_SH, '-c',
                   acquireLockCommand]
            (rc, out, err) = misc.execCmd(cmd, cwd=self.lockUtilPath,
                                          sudo=True,
                                          ioclass=utils.IOCLASS.REALTIME,
                                          ioclassdata=0, setsid=True)
            if rc != 0:
                raise se.AcquireLockFailure(self._sdUUID, rc, out, err)
            self.log.debug("Clustered lock acquired successfully")

    def inquire(self):
        raise InquireNotSupportedError()

    def getLockUtilFullPath(self):
        return os.path.join(self.lockUtilPath, self.lockCmd)

    def release(self):
        with self._lock:
            freeLockUtil = os.path.join(self.lockUtilPath, self.freeLockCmd)
            releaseLockCommand = [freeLockUtil, self._sdUUID]
            self.log.info("Releasing cluster lock for domain %s" %
                          self._sdUUID)
            (rc, out, err) = misc.execCmd(releaseLockCommand,
                                          cwd=self.lockUtilPath)
            if rc != 0:
                self.log.error("Could not release cluster lock "
                               "rc=%s out=%s, err=%s" % (str(rc), out, err))

            self.log.debug("Cluster lock released successfully")


initSANLockLog = logging.getLogger("Storage.initSANLock")


def initSANLock(sdUUID, idsPath, leasesPath):
    initSANLockLog.debug("Initializing SANLock for domain %s", sdUUID)

    try:
        sanlock.init_lockspace(sdUUID, idsPath)
        sanlock.init_resource(sdUUID, SDM_LEASE_NAME,
                              [(leasesPath, SDM_LEASE_OFFSET)])
    except sanlock.SanlockException:
        initSANLockLog.error("Cannot initialize SANLock for domain %s",
                             sdUUID, exc_info=True)
        raise se.ClusterLockInitError()


class SANLock(object):
    log = logging.getLogger("Storage.SANLock")

    _sanlock_fd = None
    _sanlock_lock = threading.Lock()

    def __init__(self, sdUUID, idsPath, leasesPath, *args):
        self._lock = threading.Lock()
        self._sdUUID = sdUUID
        self._idsPath = idsPath
        self._leasesPath = leasesPath
        self._sanlockfd = None

    def initLock(self):
        initSANLock(self._sdUUID, self._idsPath, self._leasesPath)

    def setParams(self, *args):
        pass

    def getReservedId(self):
        return MAX_HOST_ID

    def getLockDisk(self):
        return [(self._leasesPath, SDM_LEASE_OFFSET)]

    def acquireHostId(self, hostId, async):
        with self._lock:
            self.log.info("Acquiring host id for domain %s (id: %s)",
                          self._sdUUID, hostId)

            try:
                sanlock.add_lockspace(self._sdUUID, hostId, self._idsPath,
                                      async=async)
            except sanlock.SanlockException as e:
                if e.errno == os.errno.EINPROGRESS:
                    # if the request is not asynchronous wait for the ongoing
                    # lockspace operation to complete
                    if not async and not sanlock.inq_lockspace(
                            self._sdUUID, hostId, self._idsPath, wait=True):
                        raise se.AcquireHostIdFailure(self._sdUUID, e)
                    # else silently continue, the host id has been acquired
                    # or it's in the process of being acquired (async)
                elif e.errno != os.errno.EEXIST:
                    raise se.AcquireHostIdFailure(self._sdUUID, e)

            self.log.debug("Host id for domain %s successfully acquired "
                           "(id: %s)", self._sdUUID, hostId)

    def releaseHostId(self, hostId, async, unused):
        with self._lock:
            self.log.info("Releasing host id for domain %s (id: %s)",
                          self._sdUUID, hostId)

            try:
                sanlock.rem_lockspace(self._sdUUID, hostId, self._idsPath,
                                      async=async, unused=unused)
            except sanlock.SanlockException as e:
                if e.errno != os.errno.ENOENT:
                    raise se.ReleaseHostIdFailure(self._sdUUID, e)

            self.log.debug("Host id for domain %s released successfully "
                           "(id: %s)", self._sdUUID, hostId)

    def hasHostId(self, hostId):
        with self._lock:
            try:
                return sanlock.inq_lockspace(self._sdUUID,
                                             hostId, self._idsPath)
            except sanlock.SanlockException:
                self.log.debug("Unable to inquire sanlock lockspace "
                               "status, returning False", exc_info=True)
                return False

    # The hostId parameter is maintained here only for compatibility with
    # ClusterLock. We could consider to remove it in the future but keeping it
    # for logging purpose is desirable.
    def acquire(self, hostId):
        with nested(self._lock, SANLock._sanlock_lock):
            self.log.info("Acquiring cluster lock for domain %s (id: %s)",
                          self._sdUUID, hostId)

            while True:
                if SANLock._sanlock_fd is None:
                    try:
                        SANLock._sanlock_fd = sanlock.register()
                    except sanlock.SanlockException as e:
                        raise se.AcquireLockFailure(
                            self._sdUUID, e.errno,
                            "Cannot register to sanlock", str(e))

                try:
                    sanlock.acquire(self._sdUUID, SDM_LEASE_NAME,
                                    self.getLockDisk(),
                                    slkfd=SANLock._sanlock_fd)
                except sanlock.SanlockException as e:
                    if e.errno != os.errno.EPIPE:
                        raise se.AcquireLockFailure(
                            self._sdUUID, e.errno,
                            "Cannot acquire cluster lock", str(e))
                    SANLock._sanlock_fd = None
                    continue

                break

            self.log.debug("Cluster lock for domain %s successfully acquired "
                           "(id: %s)", self._sdUUID, hostId)

    def inquire(self):
        resource = sanlock.read_resource(self._leasesPath, SDM_LEASE_OFFSET)
        owners = sanlock.read_resource_owners(self._sdUUID, SDM_LEASE_NAME,
                                              self.getLockDisk())

        if len(owners) == 1:
            return resource.get("version"), owners[0].get("host_id")
        elif len(owners) > 1:
            self.log.error("Cluster lock is reported to have more than "
                           "one owner: %s", owners)
            raise RuntimeError("Cluster lock multiple owners error")

        return None, None

    def release(self):
        with self._lock:
            self.log.info("Releasing cluster lock for domain %s", self._sdUUID)

            try:
                sanlock.release(self._sdUUID, SDM_LEASE_NAME,
                                self.getLockDisk(), slkfd=SANLock._sanlock_fd)
            except sanlock.SanlockException as e:
                raise se.ReleaseLockFailure(self._sdUUID, e)

            self._sanlockfd = None
            self.log.debug("Cluster lock for domain %s successfully released",
                           self._sdUUID)


class LocalLock(object):
    log = logging.getLogger("Storage.LocalLock")

    LVER = 0

    _globalLockMap = {}
    _globalLockMapSync = threading.Lock()

    def __init__(self, sdUUID, idsPath, leasesPath, *args):
        self._sdUUID = sdUUID
        self._idsPath = idsPath
        self._leasesPath = leasesPath

    def initLock(self):
        # The LocalLock initialization is based on SANLock to maintain on-disk
        # domain format consistent across all the V3 types.
        # The advantage is that the domain can be exposed as an NFS/GlusterFS
        # domain later on without any modification.
        # XXX: Keep in mind that LocalLock and SANLock cannot detect each other
        # and therefore concurrently using the same domain as local domain and
        # NFS domain (or any other shared file-based domain) will certainly
        # lead to disastrous consequences.
        initSANLock(self._sdUUID, self._idsPath, self._leasesPath)

    def setParams(self, *args):
        pass

    def getReservedId(self):
        return MAX_HOST_ID

    def _getLease(self):
        return self._globalLockMap.get(self._sdUUID, (None, None))

    def acquireHostId(self, hostId, async):
        with self._globalLockMapSync:
            currentHostId, lockFile = self._getLease()

            if currentHostId is not None and currentHostId != hostId:
                self.log.error("Different host id already acquired (id: %s)",
                               currentHostId)
                raise se.AcquireHostIdFailure(self._sdUUID)

            self._globalLockMap[self._sdUUID] = (hostId, lockFile)

        self.log.debug("Host id for domain %s successfully acquired (id: %s)",
                       self._sdUUID, hostId)

    def releaseHostId(self, hostId, async, unused):
        with self._globalLockMapSync:
            currentHostId, lockFile = self._getLease()

            if currentHostId is not None and currentHostId != hostId:
                self.log.error("Different host id acquired (id: %s)",
                               currentHostId)
                raise se.ReleaseHostIdFailure(self._sdUUID)

            if lockFile is not None:
                self.log.error("Cannot release host id when lock is acquired")
                raise se.ReleaseHostIdFailure(self._sdUUID)

            del self._globalLockMap[self._sdUUID]

        self.log.debug("Host id for domain %s released successfully (id: %s)",
                       self._sdUUID, hostId)

    def hasHostId(self, hostId):
        with self._globalLockMapSync:
            currentHostId, lockFile = self._getLease()
            return currentHostId == hostId

    def acquire(self, hostId):
        with self._globalLockMapSync:
            self.log.info("Acquiring local lock for domain %s (id: %s)",
                          self._sdUUID, hostId)

            hostId, lockFile = self._getLease()

            if lockFile:
                try:
                    misc.NoIntrCall(fcntl.fcntl, lockFile, fcntl.F_GETFD)
                except IOError as e:
                    # We found a stale file descriptor, removing.
                    del self._globalLockMap[self._sdUUID]

                    # Raise any other unkown error.
                    if e.errno != os.errno.EBADF:
                        raise
                else:
                    self.log.debug("Local lock already acquired for domain "
                                   "%s (id: %s)", self._sdUUID, hostId)
                    return  # success, the lock was already acquired

            lockFile = misc.NoIntrCall(os.open, self._idsPath, os.O_RDONLY)

            try:
                misc.NoIntrCall(fcntl.flock, lockFile,
                                fcntl.LOCK_EX | fcntl.LOCK_NB)
            except IOError as e:
                misc.NoIntrCall(os.close, lockFile)
                if e.errno in (os.errno.EACCES, os.errno.EAGAIN):
                    raise se.AcquireLockFailure(
                        self._sdUUID, e.errno, "Cannot acquire local lock",
                        str(e))
                raise
            else:
                self._globalLockMap[self._sdUUID] = (hostId, lockFile)

        self.log.debug("Local lock for domain %s successfully acquired "
                       "(id: %s)", self._sdUUID, hostId)

    def inquire(self):
        with self._globalLockMapSync:
            hostId, lockFile = self._getLease()
            return self.LVER, hostId if lockFile else None

    def release(self):
        with self._globalLockMapSync:
            self.log.info("Releasing local lock for domain %s", self._sdUUID)

            hostId, lockFile = self._getLease()

            if not lockFile:
                self.log.debug("Local lock already released for domain %s",
                               self._sdUUID)
                return

            misc.NoIntrCall(os.close, lockFile)
            self._globalLockMap[self._sdUUID] = (hostId, None)

            self.log.debug("Local lock for domain %s successfully released",
                           self._sdUUID)
