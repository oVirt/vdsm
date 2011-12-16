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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301  USA
#
# Refer to the README and COPYING files for full details of the license
#

import os.path
from config import config
import misc
import subprocess
import constants
import storage_exception as se
import threading
import logging

MAX_HOST_ID = 250

class ClusterLock(object):
    log = logging.getLogger("ClusterLock")
    lockUtilPath = config.get('irs', 'lock_util_path')
    lockCmd = config.get('irs', 'lock_cmd')
    freeLockCmd = config.get('irs', 'free_lock_cmd')

    def __init__(self, sdUUID, idFile, leaseFile,
            lockRenewalIntervalSec,
            leaseTimeSec,
            leaseFailRetry,
            ioOpTimeoutSec ):
        self._lock = threading.Lock()
        self._sdUUID = sdUUID
        self._leaseFile = leaseFile
        self.setParams(lockRenewalIntervalSec, leaseTimeSec,
                       leaseFailRetry, ioOpTimeoutSec)
        self.__hostID = None

    def initLock(self):
        lockUtil = os.path.join(self.lockUtilPath, "safelease")
        initCommand = [ lockUtil, "release", "-f", self._leaseFile, "0" ]
        rc, out, err = misc.execCmd(initCommand, sudo=False, cwd=self.lockUtilPath)
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
            self.log.debug("Acquiring cluster lock for domain %s" % self._sdUUID)

            lockUtil = os.path.join(self.lockUtilPath, self.lockCmd)
            acquireLockCommand = subprocess.list2cmdline([lockUtil, "start", self._sdUUID, str(hostID),
                                            str(self._lockRenewalIntervalSec), str(self._leaseFile), str(leaseTimeMs),
                                            str(ioOpTimeoutMs), str(self._leaseFailRetry)])
            cmd = [constants.EXT_SETSID, constants.EXT_IONICE, '-c1', '-n0',
                constants.EXT_SU, misc.IOUSER, '-s', constants.EXT_SH, '-c',
                acquireLockCommand]
            (rc, out, err) = misc.execCmd(cmd, cwd=self.lockUtilPath)
            if rc != 0:
                raise se.AcquireLockFailure(self._sdUUID, rc, out, err)
            self.__hostID = hostID
            self.log.debug("Clustered lock acquired successfully")

    @property
    def locked(self):
        with self._lock:
            # TODO : implement something more advanced like
            # monitoring the spmprotect proc
            return self.__hostID is not None

    def release(self):
        with self._lock:
            if self.__hostID is None:
                # TODO : raise proper exception
                raise Exception("Cluster lock not locked for domain `%s`, cannot release" % self._sdUUID)

            self.__hostID = None
            freeLockUtil = os.path.join(self.lockUtilPath, self.freeLockCmd)
            releaseLockCommand = [ freeLockUtil, self._sdUUID ]
            self.log.info("Releasing cluster lock for domain %s" % (self._sdUUID))
            (rc, out, err) = misc.execCmd(releaseLockCommand, sudo=False, cwd=self.lockUtilPath)
            if rc != 0:
                self.log.error("Could not release cluster lock rc=%s out=%s, err=%s" % (str(rc), out, err))
            self.log.debug("Cluster lock released successfully")
