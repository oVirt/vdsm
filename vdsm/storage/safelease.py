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
    def __init__(self, sdUUID, leaseFile,
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

    @classmethod
    def initLock(cls, path):
        lockUtil = os.path.join(cls.lockUtilPath, "safelease")
        initCommand = [ lockUtil, "release", "-f", str(path), "0" ]
        rc, out, err = misc.execCmd(initCommand, sudo=False, cwd=cls.lockUtilPath)
        if rc != 0:
            cls.log.warn("could not initialise spm lease (%s): %s", rc, out)
            raise se.ClusterLockInitError()


    def setParams(self, lockRenewalIntervalSec,
                    leaseTimeSec,
                    leaseFailRetry,
                    ioOpTimeoutSec):
        self._lockRenewalIntervalSec = lockRenewalIntervalSec
        self._leaseTimeSec = leaseTimeSec
        self._leaseFailRetry = leaseFailRetry
        self._ioOpTimeoutSec = ioOpTimeoutSec

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
