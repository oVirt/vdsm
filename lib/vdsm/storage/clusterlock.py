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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#

from __future__ import absolute_import

import errno
import fcntl
import collections
import logging
import os
import subprocess
import threading
import time

from vdsm import constants
from vdsm import utils

from vdsm.common import concurrent
from vdsm.common import errors
from vdsm.common import osutils
from vdsm.config import config
from vdsm.storage import constants as sc
from vdsm.storage import exception as se
from vdsm.storage import misc
from vdsm.storage.compat import sanlock


MAX_HOST_ID = 250

# Host status - currently only sanlock supports this, and the documentaion
# describes the sanlock implementation. For more info see:
# https://git.fedorahosted.org/cgit/sanlock.git/tree/src/lockspace.c

# Cannot tell because clusterlock does not implement this or call failed.
HOST_STATUS_UNAVAILABLE = "unavailable"

# Host has a lease on the storage, but the clusterlock cannot tell if the host
# is live or dead yet. Would typically last for 10-20 seconds, but it's
# possible that this could persist for up to 80 seconds before host is
# considered live or fail.
HOST_STATUS_UNKNOWN = "unknown"

# There is no lease for this host id.
HOST_STATUS_FREE = "free"

# Host has renewed its lease in the last 80 seconds. It may be renewing its
# lease now or not, we can tell that only by checking again later.
HOST_STATUS_LIVE = "live"

# Host has not renewed its lease for 80 seconds. Would last for 60 seconds
# before host is considered dead.
HOST_STATUS_FAIL = "fail"

# Host has not renewed its lease for 140 seconds.
HOST_STATUS_DEAD = "dead"


class Error(errors.Base):
    """ Base class for clusterlock errors. """


class InvalidLeaseName(Error):
    """
    Raise when lease name does not match sanlock resource name on storage.

    After legacy cold merge, we used to rename the temporary "<uuid>_MERGE"
    volume to "<uuid>". However, the volume lease was not updated, and still
    carry the old name "<uuid>_MERGE". Sanlock does not allow acquiring a lease
    or even querying a lease with the wrong lease name.  Because the lease name
    does not match the resource, this resource is not usable, and any flow
    trying to take a volume lease will fail.  Practically, this means this
    volume does not have a lease.

    We plan to fix the legacy cold merge code causing this, but the bad leases
    are out in the wild, and we must handle them.
    """

    msg = ("Sanlock resource name {self.resource} does not match lease name "
           "{self.lease}, this lease must be repaired.")

    def __init__(self, resource, lease):
        self.resource = resource
        self.lease = lease


class MultipleLeasesNotSupported(Error):
    """
    Raised when trying to use multiple leases on a cluster lock that
    supports only single lease.
    """

    msg = "Mulitple leases not supported, cannot {self.action} {self.lease}"

    def __init__(self, action, lease):
        self.action = action
        self.lease = lease


class TemporaryFailure(Error):
    msg = "Cannot {self.action} {self.lease}: {self.reason}"

    def __init__(self, action, lease, reason):
        self.action = action
        self.lease = lease
        self.reason = reason


Lease = collections.namedtuple("Lease", "name, path, offset")


class SafeLease(object):
    log = logging.getLogger("storage.Safelease")

    lockUtilPath = config.get('irs', 'lock_util_path')
    lockCmd = config.get('irs', 'lock_cmd')
    freeLockCmd = config.get('irs', 'free_lock_cmd')

    def __init__(self, sdUUID, idsPath, lease, lockRenewalIntervalSec,
                 leaseTimeSec, leaseFailRetry, ioOpTimeoutSec, **kwargs):
        """
        Note: kwargs are not used. They were added to keep forward
              compatibility with more recent locks.
        """
        self._lock = threading.Lock()
        self._sdUUID = sdUUID
        self._idsPath = idsPath
        self._lease = lease
        self.setParams(lockRenewalIntervalSec, leaseTimeSec, leaseFailRetry,
                       ioOpTimeoutSec)

    @property
    def supports_multiple_leases(self):
        return False

    def initLock(self, lease):
        if lease != self._lease:
            raise MultipleLeasesNotSupported("init", lease)
        lockUtil = constants.EXT_SAFELEASE
        initCommand = [lockUtil, "release", "-f", lease.path, "0"]
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

    def acquireHostId(self, hostId, wait):
        self.log.debug("Host id for domain %s successfully acquired (id: %s)",
                       self._sdUUID, hostId)

    def releaseHostId(self, hostId, wait, unused):
        self.log.debug("Host id for domain %s released successfully (id: %s)",
                       self._sdUUID, hostId)

    def hasHostId(self, hostId):
        return True

    def getHostStatus(self, hostId):
        return HOST_STATUS_UNAVAILABLE

    def acquire(self, hostID, lease):
        if lease != self._lease:
            raise MultipleLeasesNotSupported("acquire", lease)
        leaseTimeMs = self._leaseTimeSec * 1000
        ioOpTimeoutMs = self._ioOpTimeoutSec * 1000
        with self._lock:
            self.log.debug("Acquiring cluster lock for domain %s" %
                           self._sdUUID)

            lockUtil = self.getLockUtilFullPath()
            acquireLockCommand = subprocess.list2cmdline([
                lockUtil, "start", self._sdUUID, str(hostID),
                str(self._lockRenewalIntervalSec), str(lease.path),
                str(leaseTimeMs), str(ioOpTimeoutMs),
                str(self._leaseFailRetry), str(os.getpid())
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

    def inquire(self, lease):
        raise se.InquireNotSupportedError()

    def getLockUtilFullPath(self):
        return os.path.join(self.lockUtilPath, self.lockCmd)

    def release(self, lease):
        if lease != self._lease:
            raise MultipleLeasesNotSupported("release", lease)
        with self._lock:
            freeLockUtil = os.path.join(self.lockUtilPath, self.freeLockCmd)
            releaseLockCommand = [freeLockUtil, self._sdUUID]
            self.log.info("Releasing cluster lock for domain %s" %
                          self._sdUUID)
            (rc, out, err) = misc.execCmd(releaseLockCommand, raw=True,
                                          cwd=self.lockUtilPath)
            if rc != 0:
                # TODO: should raise
                self.log.error("Could not release cluster lock for domain %s "
                               "(rc=%d, out=%s, err=%s)" %
                               (self._sdUUID, rc, out, err))
                return

            self.log.debug("Cluster lock for domain %s released successfully",
                           self._sdUUID)


class SANLock(object):

    STATUS_NAME = {
        sanlock.HOST_UNKNOWN: HOST_STATUS_UNKNOWN,
        sanlock.HOST_FREE: HOST_STATUS_FREE,
        sanlock.HOST_LIVE: HOST_STATUS_LIVE,
        sanlock.HOST_FAIL: HOST_STATUS_FAIL,
        sanlock.HOST_DEAD: HOST_STATUS_DEAD,
    }

    # Acquiring a host id takes about 20-30 seconds when all is good, but it
    # may take 2-3 minutes if a host was not shutdown properly (.e.g sanlock
    # was killed).
    ACQUIRE_HOST_ID_TIMEOUT = 180

    log = logging.getLogger("storage.SANLock")

    _io_timeout = config.getint('sanlock', 'io_timeout')
    _sanlock_fd = None
    _sanlock_lock = threading.Lock()

    def __init__(self, sdUUID, idsPath, lease, *args, **kwargs):
        """
        Note: lease and args are unused, needed by legacy locks.
        """
        self._lock = threading.Lock()
        self._sdUUID = sdUUID
        self._idsPath = idsPath
        self._alignment = kwargs.get("alignment", sc.ALIGNMENT_1M)
        self._block_size = kwargs.get("block_size", sc.BLOCK_SIZE_512)
        self._ready = concurrent.ValidatingEvent()
        self._add_lockspace_start = None

    @property
    def supports_multiple_leases(self):
        return True

    @property
    def _lockspace_name(self):
        return self._sdUUID.encode("utf-8")

    def initLock(self, lease):
        self.log.info(
            "Initializing sanlock for domain %s path=%s alignment=%s "
            "block_size=%s io_timeout=%s",
            self._sdUUID, self._idsPath, self._alignment, self._block_size,
            self._io_timeout)

        resource_name = lease.name.encode("utf-8")
        try:
            sanlock.write_lockspace(
                self._lockspace_name,
                self._idsPath,
                iotimeout=self._io_timeout,
                align=self._alignment,
                sector=self._block_size)

            sanlock.write_resource(
                self._lockspace_name,
                resource_name,
                [(lease.path, lease.offset)],
                align=self._alignment,
                sector=self._block_size)
        except sanlock.SanlockException:
            self.log.exception(
                "Cannot initialize lock for domain %s", self._sdUUID)
            raise se.ClusterLockInitError()

    def setParams(self, *args):
        pass

    def getReservedId(self):
        return MAX_HOST_ID

    def acquireHostId(self, hostId, wait):
        self.log.info("Acquiring host id for domain %s (id=%s, wait=%s)",
                      self._sdUUID, hostId, wait)

        # Ensure that future calls to acquire() will wait until host id is
        # acquired.
        self._ready.valid = True

        with self._lock:
            self._start_add_lockspace()
            try:
                sanlock.add_lockspace(
                    self._lockspace_name,
                    hostId,
                    self._idsPath,
                    iotimeout=self._io_timeout,
                    wait=wait)
            except sanlock.SanlockException as e:
                if e.errno == errno.EINPROGRESS:
                    # if the request is not asynchronous wait for the ongoing
                    # lockspace operation to complete else silently continue,
                    # the host id has been acquired or it's in the process of
                    # being acquired (async).
                    if wait:
                        if not sanlock.inq_lockspace(
                                self._lockspace_name,
                                hostId,
                                self._idsPath,
                                wait=True):
                            raise se.AcquireHostIdFailure(self._sdUUID, e)

                        self._end_add_lockspace(hostId)
                        self._ready.set()
                elif e.errno == errno.EEXIST:
                    self.log.info("Host id %s for domain %s already acquired",
                                  hostId, self._sdUUID)
                    self._cancel_add_lockspace()
                    self._ready.set()
                else:
                    self._cancel_add_lockspace()
                    raise se.AcquireHostIdFailure(self._sdUUID, e)
            else:
                if wait:
                    self._end_add_lockspace(hostId)
                    self._ready.set()

    def _start_add_lockspace(self):
        """
        Start add_lockspace timer unless if is already running. Must be called
        when self._lock is locked.
        """
        if self._add_lockspace_start is None:
            self._add_lockspace_start = time.monotonic()

    def _cancel_add_lockspace(self):
        self._add_lockspace_start = None

    def _end_add_lockspace(self, hostId):
        """
        If add_lockspace was in progress, end the timer and log acquire
        message. Must be called when self._lock is locked.
        """
        if self._add_lockspace_start is None:
            return

        elapsed = time.monotonic() - self._add_lockspace_start
        self._add_lockspace_start = None

        self.log.info(
            "Host id %s for domain %s acquired in %d seconds",
            hostId, self._sdUUID, elapsed)

    def releaseHostId(self, hostId, wait, unused):
        self.log.info("Releasing host id for domain %s (id: %s)",
                      self._sdUUID, hostId)

        # Ensure that future calls to acquire() will fail quickly.
        self._ready.valid = False

        with self._lock:
            try:
                sanlock.rem_lockspace(self._lockspace_name, hostId,
                                      self._idsPath, unused=unused,
                                      wait=wait)
            except sanlock.SanlockException as e:
                if e.errno != errno.ENOENT:
                    raise se.ReleaseHostIdFailure(self._sdUUID, e)

        self.log.info("Host id for domain %s released successfully "
                      "(id: %s)", self._sdUUID, hostId)

    def hasHostId(self, hostId):
        with self._lock:
            try:
                has_host_id = sanlock.inq_lockspace(
                    self._lockspace_name, hostId, self._idsPath)
            except sanlock.SanlockException:
                self.log.debug("Unable to inquire sanlock lockspace "
                               "status, returning False", exc_info=True)
                return False

            if has_host_id:
                # Host id was acquired. Wake up threads waiting in acquire().
                self._ready.set()
                self._end_add_lockspace(hostId)
            else:
                if self._ready.valid and self._ready.is_set():
                    # Host id was released by sanlock. This happens after
                    # renewal failure when storage is inaccessible.
                    self.log.warning("Host id %s for domain %s was released",
                                     hostId, self._sdUUID)
                self._ready.clear()

        return has_host_id

    def getHostStatus(self, hostId):
        try:
            hosts = sanlock.get_hosts(self._lockspace_name, hostId)
        except sanlock.SanlockException as e:
            self.log.debug("Unable to get host %d status in lockspace %s: %s",
                           hostId, self._sdUUID, e)
            return HOST_STATUS_UNAVAILABLE
        else:
            status = hosts[0]['flags']
            return self.STATUS_NAME[status]

    # The hostId parameter is maintained here only for compatibility with
    # ClusterLock. We could consider to remove it in the future but keeping it
    # for logging purpose is desirable.
    def acquire(self, hostId, lease):
        self.log.info("Acquiring %s for host id %s", lease, hostId)

        # If host id was acquired by this thread, this will return immediately.
        # If host is id being acquired asynchronically by the domain monitor,
        # wait until the domain monitor find that host id was acquired.
        #
        # IMPORTANT: This must be done *before* entering the lock. Once we
        # enter the lock, the domain monitor cannot check if host id was
        # acquired, since hasHostId() is using the same lock.
        if not self._ready.wait(self.ACQUIRE_HOST_ID_TIMEOUT):
            raise se.AcquireHostIdFailure(
                "Timeout acquiring host id, cannot acquire %s (id=%s)"
                % (lease, hostId))

        with self._lock, SANLock._sanlock_lock:
            while True:
                if SANLock._sanlock_fd is None:
                    try:
                        SANLock._sanlock_fd = sanlock.register()
                    except sanlock.SanlockException as e:
                        raise se.AcquireLockFailure(
                            self._sdUUID, e.errno,
                            "Cannot register to sanlock", str(e))

                resource_name = lease.name.encode("utf-8")
                try:
                    sanlock.acquire(self._lockspace_name, resource_name,
                                    [(lease.path, lease.offset)],
                                    slkfd=SANLock._sanlock_fd)
                except sanlock.SanlockException as e:
                    if e.errno != errno.EPIPE:
                        raise se.AcquireLockFailure(
                            self._sdUUID, e.errno,
                            "Cannot acquire %s" % (lease,), str(e))
                    SANLock._sanlock_fd = None
                    continue

                break

        self.log.info("Successfully acquired %s for host id %s", lease, hostId)

    def inquire(self, lease):
        resource = sanlock.read_resource(
            lease.path,
            lease.offset,
            align=self._alignment,
            sector=self._block_size)

        resource_name = lease.name.encode("utf-8")
        if resource["resource"] != resource_name:
            raise InvalidLeaseName(resource["resource"], lease)

        owners = sanlock.read_resource_owners(
            self._lockspace_name,
            resource_name,
            [(lease.path, lease.offset)],
            align=self._alignment,
            sector=self._block_size)

        if len(owners) > 1:
            self.log.error("Cluster lock is reported to have more than "
                           "one owner: %s", owners)
            raise RuntimeError("Multiple owners for %s" % (lease,))

        elif not owners:
            return None, None

        resource_owner = owners[0]
        resource_version = resource["version"]
        host_id = resource_owner["host_id"]
        try:
            host = sanlock.get_hosts(self._lockspace_name, host_id)[0]
        except sanlock.SanlockException as e:
            if e.errno == errno.ENOENT:
                # add_lockspace has not been completed yet,
                # the inquiry has to be retried.
                raise TemporaryFailure("inquire", lease, str(e))
            elif e.errno == errno.EAGAIN:
                # The host status is not available yet.
                # Normally, we'd raise it to the caller, but this
                # breaks the "Remove DC" flow in engine, so we assume
                # the lease is currently held by the host
                # See: https://bugzilla.redhat.com/1613838
                self.log.debug("host %s status in not available yet, "
                               "it may hold the lease %s",
                               host_id, lease)
                return resource_version, host_id
            else:
                raise

        host_status = self.STATUS_NAME[host["flags"]]

        if resource_owner["generation"] != host["generation"]:
            # The lease is considered free by sanlock because
            # the host reconnected to the storage but it no
            # longer has the lease
            self.log.debug("host %r generation %r does not match resource "
                           "generation %r, lease %s is free", host_id,
                           host, resource_owner["generation"], lease)
            return resource_version, None

        if host_status in (HOST_STATUS_DEAD, HOST_STATUS_FREE):
            # These are the only states that mean the host cannot hold
            # this lease. Any other state means the host either holding the
            # lease or could be holding the lease.
            self.log.debug("host %s cannot hold %s is effectively free",
                           host, lease)
            return resource_version, None

        return resource_version, host_id

    def release(self, lease):
        self.log.info("Releasing %s", lease)
        with self._lock:
            resource_name = lease.name.encode("utf-8")
            try:
                sanlock.release(self._lockspace_name, resource_name,
                                [(lease.path, lease.offset)],
                                slkfd=SANLock._sanlock_fd)
            except sanlock.SanlockException as e:
                raise se.ReleaseLockFailure(self._sdUUID, e)

        self.log.info("Successfully released %s", lease)


class LocalLock(object):
    log = logging.getLogger("storage.LocalLock")

    LVER = 0

    _globalLockMap = {}
    _globalLockMapSync = threading.Lock()

    def __init__(self, sdUUID, idsPath, lease, *args, **kwargs):
        """
        Note: args unused, needed only by legacy locks. kwargs also unused,
              needed only by distributed locks like sanlock.
        """
        self._sdUUID = sdUUID
        self._idsPath = idsPath
        self._lease = lease

    @property
    def supports_multiple_leases(self):
        # Current implemention use single lock using the ids file (see
        # _getLease). We can support multiple leases, but I'm not sure if there
        # is any value in local volume leases.
        return False

    def initLock(self, lease):
        if lease != self._lease:
            raise MultipleLeasesNotSupported("init", lease)

    def setParams(self, *args):
        pass

    def getReservedId(self):
        return MAX_HOST_ID

    def _getLease(self):
        return self._globalLockMap.get(self._sdUUID, (None, None))

    def acquireHostId(self, hostId, wait):
        with self._globalLockMapSync:
            currentHostId, lockFile = self._getLease()

            if currentHostId is not None and currentHostId != hostId:
                self.log.error("Different host id already acquired (id: %s)",
                               currentHostId)
                raise se.AcquireHostIdFailure(self._sdUUID)

            self._globalLockMap[self._sdUUID] = (hostId, lockFile)

        self.log.debug("Host id for domain %s successfully acquired (id: %s)",
                       self._sdUUID, hostId)

    def releaseHostId(self, hostId, wait, unused):
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

    def getHostStatus(self, hostId):
        return HOST_STATUS_UNAVAILABLE

    def acquire(self, hostId, lease):
        if lease != self._lease:
            raise MultipleLeasesNotSupported("acquire", lease)
        with self._globalLockMapSync:
            self.log.info("Acquiring local lock for domain %s (id: %s)",
                          self._sdUUID, hostId)

            hostId, lockFile = self._getLease()

            if lockFile:
                try:
                    osutils.uninterruptible(fcntl.fcntl, lockFile,
                                            fcntl.F_GETFD)
                except IOError as e:
                    # We found a stale file descriptor, removing.
                    del self._globalLockMap[self._sdUUID]

                    # Raise any other unkown error.
                    if e.errno != errno.EBADF:
                        raise
                else:
                    self.log.debug("Local lock already acquired for domain "
                                   "%s (id: %s)", self._sdUUID, hostId)
                    return  # success, the lock was already acquired

            lockFile = osutils.uninterruptible(os.open, self._idsPath,
                                               os.O_RDONLY)

            try:
                osutils.uninterruptible(fcntl.flock, lockFile,
                                        fcntl.LOCK_EX | fcntl.LOCK_NB)
            except IOError as e:
                osutils.close_fd(lockFile)
                if e.errno in (errno.EACCES, errno.EAGAIN):
                    raise se.AcquireLockFailure(
                        self._sdUUID, e.errno, "Cannot acquire local lock",
                        str(e))
                raise
            else:
                self._globalLockMap[self._sdUUID] = (hostId, lockFile)

        self.log.debug("Local lock for domain %s successfully acquired "
                       "(id: %s)", self._sdUUID, hostId)

    def inquire(self, lease):
        if lease != self._lease:
            raise MultipleLeasesNotSupported("inquire", lease)
        with self._globalLockMapSync:
            hostId, lockFile = self._getLease()
            return self.LVER, (hostId if lockFile else None)

    def release(self, lease):
        if lease != self._lease:
            raise MultipleLeasesNotSupported("release", lease)
        with self._globalLockMapSync:
            self.log.info("Releasing local lock for domain %s", self._sdUUID)

            hostId, lockFile = self._getLease()

            if not lockFile:
                self.log.debug("Local lock already released for domain %s",
                               self._sdUUID)
                return

            osutils.close_fd(lockFile)
            self._globalLockMap[self._sdUUID] = (hostId, None)

            self.log.debug("Local lock for domain %s successfully released",
                           self._sdUUID)


def alignment(block_size, max_hosts):
    if max_hosts < 1 or max_hosts > sc.HOSTS_MAX:
        raise se.InvalidParameterException('max_hosts', max_hosts)
    if block_size not in (sc.BLOCK_SIZE_512, sc.BLOCK_SIZE_4K):
        raise se.InvalidParameterException('block_size', block_size)

    if block_size == sc.BLOCK_SIZE_512:
        # Only this block size is supported on 512b blocks
        # Supports up to 2000 hosts.
        return sc.ALIGNMENT_1M

    if max_hosts > sc.HOSTS_4K_4M:
        return sc.ALIGNMENT_8M

    if max_hosts > sc.HOSTS_4K_2M:
        return sc.ALIGNMENT_4M

    if max_hosts > sc.HOSTS_4K_1M:
        return sc.ALIGNMENT_2M

    return sc.ALIGNMENT_1M
