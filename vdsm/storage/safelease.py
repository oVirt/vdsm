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

import os.path
from vdsm.config import config
import misc
import errno
import subprocess
import sanlock
from contextlib import nested
from vdsm import constants
import storage_exception as se
import threading
import logging

MAX_HOST_ID = 250
LEASE_NAME = 'SDM'

class ClusterLock(object):
    log = logging.getLogger("ClusterLock")
    lockUtilPath = config.get('irs', 'lock_util_path')
    lockCmd = config.get('irs', 'lock_cmd')
    freeLockCmd = config.get('irs', 'free_lock_cmd')

    def __init__(self, sdUUID, idFile, leaseFile,
            lockRenewalIntervalSec,
            leaseTimeSec,
            leaseFailRetry,
            ioOpTimeoutSec):
        self._lock = threading.RLock()
        self._sdUUID = sdUUID
        self._leaseFile = leaseFile
        self.setParams(lockRenewalIntervalSec, leaseTimeSec,
                       leaseFailRetry, ioOpTimeoutSec)

    def initLock(self):
        lockUtil = os.path.join(self.lockUtilPath, "safelease")
        initCommand = [lockUtil, "release", "-f", self._leaseFile, "0"]
        rc, out, err = misc.execCmd(initCommand, sudo=False,
                cwd=self.lockUtilPath)
        if rc != 0:
            self.log.warn("could not initialise spm lease (%s): %s", rc, out)
            raise se.ClusterLockInitError()

    def setParams(self, lockRenewalIntervalSec,
                    leaseTimeSec,
                    leaseFailRetry,
                    ioOpTimeoutSec):
        self._lockRenewalIntervalSec = lockRenewalIntervalSec
        self._leaseTimeSec = leaseTimeSec
        self._leaseFailRetry = leaseFailRetry
        self._ioOpTimeoutSec = ioOpTimeoutSec

    def getReservedId(self):
        return 1000

    def acquireHostId(self, hostID):
        pass

    def releaseHostId(self, hostID):
        pass

    def acquire(self, hostID):
        leaseTimeMs = self._leaseTimeSec * 1000
        ioOpTimeoutMs = self._ioOpTimeoutSec * 1000
        with self._lock:
            self.log.debug("Acquiring cluster lock for domain %s" %
                    self._sdUUID)

            lockUtil = self.getLockUtilFullPath()
            acquireLockCommand = subprocess.list2cmdline([lockUtil, "start",
                self._sdUUID, str(hostID), str(self._lockRenewalIntervalSec),
                str(self._leaseFile), str(leaseTimeMs), str(ioOpTimeoutMs),
                str(self._leaseFailRetry)])

            cmd = [constants.EXT_SETSID, constants.EXT_IONICE, '-c1', '-n0',
                constants.EXT_SU, misc.IOUSER, '-s', constants.EXT_SH, '-c',
                acquireLockCommand]
            (rc, out, err) = misc.execCmd(cmd, cwd=self.lockUtilPath,
                    sudo=True)
            if rc != 0:
                raise se.AcquireLockFailure(self._sdUUID, rc, out, err)
            self.log.debug("Clustered lock acquired successfully")

    def getLockUtilFullPath(self):
        return os.path.join(self.lockUtilPath, self.lockCmd)

    def release(self):
        with self._lock:
            freeLockUtil = os.path.join(self.lockUtilPath, self.freeLockCmd)
            releaseLockCommand = [freeLockUtil, self._sdUUID]
            self.log.info("Releasing cluster lock for domain %s" %
                    self._sdUUID)
            (rc, out, err) = misc.execCmd(releaseLockCommand, sudo=False,
                    cwd=self.lockUtilPath)
            if rc != 0:
                self.log.error("Could not release cluster lock " +
                        "rc=%s out=%s, err=%s" % (str(rc), out, err))

            self.log.debug("Cluster lock released successfully")


class SANLock(object):
    log = logging.getLogger("SANLock")

    _sanlock_fd = None
    _sanlock_lock = threading.Lock()

    def __init__(self, sdUUID, idsPath, leasesPath, *args):
        self._lock = threading.Lock()
        self._sdUUID = sdUUID
        self._idsPath = idsPath
        self._leasesPath = leasesPath
        self._hostId = None
        self._sanlockfd = None
        self._lockAcquired = False

    def initLock(self):
        try:
            sanlock.init_lockspace(self._sdUUID, self._idsPath)
            sanlock.init_resource(self._sdUUID, LEASE_NAME, [self._leasesPath])
        except sanlock.SanlockException:
            self.log.warn("Cannot initialize clusterlock", exc_info=True)
            raise se.ClusterLockInitError()

    def setParams(self, *args):
        pass

    def getReservedId(self):
        return MAX_HOST_ID

    def acquireHostId(self, hostId):
        with self._lock:
            if self._hostId is not None:
                raise se.AcquireHostIdFailure(self._sdUUID,
                                              "Host id already acquired")

            self.log.info("Acquiring host id for domain %s (id: %s)",
                          self._sdUUID, hostId)

            try:
                sanlock.add_lockspace(self._sdUUID, hostId, self._idsPath)
            except sanlock.SanlockException, e:
                if e.errno != errno.EEXIST:
                    raise se.AcquireHostIdFailure(self._sdUUID, e)

            self._hostId = hostId
            self.log.debug("Host id for domain %s successfully acquired "
                           "(id: %s)", self._sdUUID, self._hostId)

    def releaseHostId(self, hostId):
        with self._lock:
            self.log.info("Releasing host id for domain %s (id: %s)",
                          self._sdUUID, hostId)

            try:
                sanlock.rem_lockspace(self._sdUUID, hostId, self._idsPath)
            except sanlock.SanlockException, e:
                raise se.ReleaseHostIdFailure(self._sdUUID, e)

            self._hostId = None
            self.log.debug("Host id for domain %s released successfully "
                           "(id: %s)", self._sdUUID, self._hostId)

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
                    except sanlock.SanlockException, e:
                        raise se.AcquireLockFailure(self._sdUUID, e.errno,
                                        "Cannot register to sanlock", str(e))

                try:
                    sanlock.acquire(self._sdUUID, LEASE_NAME,
                                    [self._leasesPath],
                                    slkfd=SANLock._sanlock_fd)
                except sanlock.SanlockException, e:
                    if e.errno != errno.EPIPE:
                        raise se.AcquireLockFailure(self._sdUUID, e.errno,
                                        "Cannot acquire cluster lock", str(e))
                    SANLock._sanlock_fd = None
                    continue

                break

            self._lockAcquired = True
            self.log.debug("Cluster lock for domain %s successfully acquired "
                           "(id: %s)", self._sdUUID, hostId)

    def release(self):
        with self._lock:
            self.log.info("Releasing cluster lock for domain %s (id: %s)",
                          self._sdUUID, self._hostId)

            try:
                sanlock.release(self._sdUUID, LEASE_NAME, [self._leasesPath],
                                slkfd=SANLock._sanlock_fd)
            except sanlock.SanlockException, e:
                raise se.ReleaseLockFailure(self._sdUUID, e)

            self._sanlockfd = None
            self._lockAcquired = False
            self.log.debug("Cluster lock for domain %s successfully released "
                           "(id: %s)", self._sdUUID, self._hostId)
