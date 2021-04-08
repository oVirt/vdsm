#
# Copyright 2020 Red Hat, Inc.
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
import uuid
import xml.etree.ElementTree as ET

from functools import partial

from vdsm.common import concurrent
from vdsm.common import exception
from vdsm.common import logutils

from vdsm.virt import errors
from vdsm.virt import virdomain
from vdsm.virt.vmdevices.storage import DISK_TYPE, VolumeNotFound

import libvirt


class JobExistsError(errors.Base):
    msg = "Job {self.job_id} already exists for image {self.img_id}"

    def __init__(self, job_id, img_id):
        self.job_id = job_id
        self.img_id = img_id


class JobPivotError(errors.Base):
    msg = "Pivot failed for Job {self.job_id}: {self.reason}"

    def __init__(self, job_id, reason):
        self.job_id = job_id
        self.reason = reason


class JobNotReadyError(JobPivotError):
    msg = "Job {self.job_id} is not ready for pivot"

    def __init__(self, job_id):
        self.job_id = job_id


log = logging.getLogger("virt.livemerge")


@virdomain.expose(
    "blockCommit",
    "blockJobInfo",
    "XMLDesc"
)
class DomainAdapter:
    """
    VM wrapper class that exposes only
    libvirt merge related operations.
    """
    def __init__(self, vm):
        self._vm = vm


class Job:
    """
    Information about live merge job.
    """

    # Job states:

    # Job was created but is not tracked yet. The next state is either EXTEND
    # or COMMIT. If initialization fails, the merge request fails.
    INIT = "INIT"

    # Extending base volume - waiting for extend completion callback. This
    # state is skipped if the base volume does not need extension. Libvirt does
    # not know about the job in this state. If extend fails the job is
    # untracked, otherwise the job switches to COMMIT state.
    EXTEND = "EXTEND"

    # Commit was started - waiting until libvirt stops reporting the job, or
    # reports that the job is ready for pivot. When commit succeeds or fails,
    # the job switches to CLEANUP.
    COMMIT = "COMMIT"

    # Cleanup was started - waiting until the cleanup thread completes. When
    # cleanup is finished, the job is untracked.
    CLEANUP = "CLEANUP"

    def __init__(self, id, drive, disk, top, base, bandwidth, state=INIT,
                 extend=None, pivot=None):
        # Read only attributes.
        self._id = id
        self._drive = drive
        self._disk = disk
        self._top = top
        self._base = base
        self._bandwidth = bandwidth

        # This job was started with VIR_DOMAIN_BLOCK_JOB_TYPE_ACTIVE_COMMIT
        # flag.
        self._active_commit = (top == disk["volumeID"])

        # Changes when libvirt block job has gone, or libvirt reports the block
        # job is ready for pivot.
        self._state = state

        # Set when starting extend.
        self.extend = extend

        # Set when active commit job is ready for pivot.
        self.pivot = pivot

        # Live job info from libvirt. This info is kept between libvirt updates
        # but not persisted to vm metadata.
        self._live_info = None

    @property
    def id(self):
        return self._id

    @property
    def drive(self):
        return self._drive

    @property
    def disk(self):
        return self._disk

    @property
    def top(self):
        return self._top

    @property
    def base(self):
        return self._base

    @property
    def bandwidth(self):
        return self._bandwidth

    @property
    def active_commit(self):
        return self._active_commit

    @property
    def state(self):
        return self._state

    @state.setter
    def state(self, new_state):
        if new_state != self._state:
            log.info("Job %s switching state from %s to %s",
                     self.id, self._state, new_state)
        self._state = new_state

    def is_ready(self):
        """
        Return True if this is an active commit, and the job finished the first
        phase and ready to pivot. Note that even if the job is ready, we need
        to check the job status in the vm xml.
        """
        if not self.active_commit:
            return False

        if self.state == self.CLEANUP:
            return True

        return (self.live_info and
                self.live_info["cur"] == self.live_info["end"])

    @property
    def live_info(self):
        return self._live_info

    @live_info.setter
    def live_info(self, info):
        log.debug("Job %s live info: %s", self.id, info)
        self._live_info = info

    # Serializing jobs.

    def to_dict(self):
        return {
            "id": self.id,
            "drive": self.drive,
            "disk": self.disk,
            "base": self.base,
            "top": self.top,
            "bandwidth": self.bandwidth,
            "state": self.state,
            "extend": self.extend,
            "pivot": self.pivot,
        }

    @classmethod
    def from_dict(cls, d):
        return cls(
            id=d["id"],
            drive=d["drive"],
            disk=d["disk"],
            top=d["top"],
            base=d["base"],
            bandwidth=d["bandwidth"],
            state=d["state"],
            extend=d["extend"],
            pivot=d["pivot"],
        )

    def info(self):
        """
        Return job info for reporting to engine.
        """
        info = {
            'blockJobType': "commit",
            'drive': self.drive,
            'id': self.id,
            'imgUUID': self.disk['imageID'],
            'jobType': 'block',
        }

        if self.live_info:
            info["bandwidth"] = self.live_info["bandwidth"]
            # TODO: Check if we can use proper integers. This hack is a lefover
            # from xmlrpc that could not pass values bigger than int32_t.
            info["cur"] = str(self.live_info["cur"])
            info["end"] = str(self.live_info["end"])
        else:
            # TODO: Check if engine really need these values when we don't have
            # any info from libvirt.
            info["bandwidth"] = 0
            info["cur"] = "0"
            info["end"] = "0"

        return info


class DriveMerger:

    # Extend takes normally 2-6 seconds, but if a host is overloaded it
    # can be much slower.
    EXTEND_TIMEOUT = 10.0
    EXTEND_ATTEMPTS = 10

    def __init__(self, vm):
        self._vm = vm
        self._dom = DomainAdapter(vm)
        self._lock = threading.RLock()
        self._jobs = {}
        self._cleanup_threads = {}

    def merge(self, driveSpec, base, top, bandwidth, job_id):
        bandwidth = int(bandwidth)
        if job_id is None:
            job_id = str(uuid.uuid4())

        try:
            drive = self._vm.findDriveByUUIDs(driveSpec)
        except LookupError:
            raise exception.ImageFileNotFound(
                "Cannot find drive", driveSpec=driveSpec, job=job_id)

        job = self._create_job(job_id, drive, base, top, bandwidth)

        try:
            base_info = self._vm.getVolumeInfo(
                drive.domainID, drive.poolID, drive.imageID, job.base)
            top_info = self._vm.getVolumeInfo(
                drive.domainID, drive.poolID, drive.imageID, job.top)
        except errors.StorageUnavailableError as e:
            raise exception.MergeFailed(
                str(e), top=top, base=job.base, job=job_id)

        if base_info['voltype'] == 'SHARED':
            raise exception.MergeFailed(
                "Base volume is shared", base_info=base_info, job=job_id)

        self._validate_base_size(drive, base_info, top_info)

        if self._base_needs_refresh(drive, base_info):
            self._refresh_base(drive, base_info)

        with self._lock:
            try:
                self._track_job(job, drive)
            except JobExistsError as e:
                raise exception.MergeFailed(str(e), job=job.id)

            if self._base_needs_extend(drive, job, base_info):
                job.extend = {
                    "attempt": 1,
                    "base_size": int(base_info["apparentsize"]),
                    "top_size": int(top_info["apparentsize"]),
                }
                self._start_extend(drive, job)
            else:
                self._start_commit(drive, job)

        # Trigger the collection of stats before returning so that callers
        # of getVmStats after this returns will see the new job
        self._vm.updateVmJobs()

    def _validate_base_size(self, drive, base_info, top_info):
        # If the drive was resized the top volume could be larger than the
        # base volume.  Libvirt can handle this situation for file-based
        # volumes and block qcow volumes (where extension happens dynamically).
        # Raw block volumes cannot be extended by libvirt so we require ovirt
        # engine to extend them before calling merge.  Check here.
        if drive.diskType != DISK_TYPE.BLOCK or base_info['format'] != 'RAW':
            return

        if int(base_info['capacity']) < int(top_info['capacity']):
            raise exception.DestinationVolumeTooSmall(
                "The base volume is undersized and cannot be extended",
                base_capacity=base_info["capacity"],
                top_capacity=top_info["capacity"])

    def _base_needs_refresh(self, drive, base_info):
        # If the base volume format is RAW and its size is smaller than its
        # capacity (this could happen because the engine extended the base
        # volume), we have to refresh the volume to cause lvm to get current lv
        # size from storage, and update the kernel so the lv reflects the real
        # size on storage. Not refreshing the volume may fail live merge.
        # This could happen if disk extended after taking a snapshot but before
        # performing the live merge.  See https://bugzilla.redhat.com/1367281
        return (drive.chunked and
                base_info['format'] == 'RAW' and
                int(base_info['apparentsize']) < int(base_info['capacity']))

    def _refresh_base(self, drive, base_info):
        log.info(
            "Refreshing base volume %r (apparentsize=%s, capacity=%s)",
            base_info['uuid'], base_info['apparentsize'],
            base_info['capacity'])

        self._vm.refresh_drive_volume({
            'domainID': drive.domainID,
            'imageID': drive.imageID,
            'name': drive.name,
            'poolID': drive.poolID,
            'volumeID': base_info['uuid'],
        })

    def _start_commit(self, drive, job):
        """
        Start libvirt blockCommit block job.

        Must be called under self._lock.
        """
        # Persist the job before starting the commit, to ensure that vdsm will
        # know about the commit if it was killed after the block job was
        # started.
        job.state = Job.COMMIT
        self._persist_jobs()

        # Check that libvirt exposes full volume chain information
        chains = self._vm.drive_get_actual_volume_chain([drive])
        if drive['alias'] not in chains:
            self._untrack_job(job.id)
            raise exception.MergeFailed(
                "Libvirt does not support volume chain monitoring",
                drive=drive, alias=drive["alias"], chains=chains, job=job.id)

        actual_chain = chains[drive['alias']]
        try:
            base_target = drive.volume_target(job.base, actual_chain)
            top_target = drive.volume_target(job.top, actual_chain)
        except VolumeNotFound as e:
            self._untrack_job(job.id)
            raise exception.MergeFailed(
                str(e), top=job.top, base=job.base, chain=actual_chain,
                job=job.id)

        # Indicate that we expect libvirt to maintain the relative paths of
        # backing files. This is necessary to ensure that a volume chain is
        # visible from any host even if the mountpoint is different.
        flags = libvirt.VIR_DOMAIN_BLOCK_COMMIT_RELATIVE

        if job.top == drive.volumeID:
            # Pass a flag to libvirt to indicate that we expect a two phase
            # block job. In the first phase, data is copied to base. Once
            # completed, an event is raised to indicate that the job has
            # transitioned to the second phase. We must then tell libvirt to
            # pivot to the new active layer (base).
            flags |= libvirt.VIR_DOMAIN_BLOCK_COMMIT_ACTIVE

        orig_chain = [entry.uuid for entry in actual_chain]
        chain_str = logutils.volume_chain_to_str(orig_chain)
        log.info("Starting merge with job_id=%r, original chain=%s, "
                 "disk=%r, base=%r, top=%r, bandwidth=%d, flags=%d",
                 job.id, chain_str, drive.name, base_target,
                 top_target, job.bandwidth, flags)

        try:
            # pylint: disable=no-member
            self._dom.blockCommit(
                drive.name,
                base_target,
                top_target,
                job.bandwidth,
                flags=flags)
        except libvirt.libvirtError as e:
            self._untrack_job(job.id)
            raise exception.MergeFailed(str(e), job=job.id)

    def _base_needs_extend(self, drive, job, base_info):
        # blockCommit will cause data to be written into the base volume.
        # Perform an initial extension to ensure there is enough space to
        # copy all the required data.  Normally we'd use monitoring to extend
        # the volume on-demand but internal watermark information is not being
        # reported by libvirt so we must do the full extension up front.  In
        # the worst case, the allocated size of 'base' should be increased by
        # the allocated size of 'top' plus one additional chunk to accomodate
        # additional writes to 'top' during the live merge operation.
        if not drive.chunked or base_info['format'] != 'COW':
            log.debug("Base volume does not support extending "
                      "job=%s drive=%s volume=%s",
                      job.id, job.drive, job.base)
            return False

        max_size = drive.getMaxVolumeSize(int(base_info["capacity"]))

        # Size can be bigger than maximum value due to rounding to LVM extent
        # size (128 MiB).
        if int(base_info["apparentsize"]) >= max_size:
            log.debug("Base volume is already extended to maximum size "
                      "job=%s drive=%s volume=%s size=%s",
                      job.id, job.drive, job.base, base_info["apparentsize"])
            return False

        return True

    def _start_extend(self, drive, job):
        """
        Start extend operation for the base volume.

        Must be called under self._lock.
        """
        # Persist the job before starting the extend, to ensure that vdsm will
        # know about the extend if it was killed after extend started.
        job.state = Job.EXTEND
        job.extend["started"] = time.monotonic()
        self._persist_jobs()

        capacity, alloc, physical = self._vm.getExtendInfo(drive)
        max_alloc = job.extend["base_size"] + job.extend["top_size"]

        log.info("Starting extend %s/%s for job=%s drive=%s volume=%s",
                 job.extend["attempt"], self.EXTEND_ATTEMPTS, job.id,
                 drive.name, job.base)

        callback = partial(self._extend_completed, job_id=job.id)
        self._vm.extendDriveVolume(
            drive, job.base, max_alloc, capacity, callback=callback)

    def _retry_extend(self, job):
        """
        Retry extend after a timeout of extend error.
        """
        assert job.extend["attempt"] < self.EXTEND_ATTEMPTS

        try:
            drive = self._vm.findDriveByUUIDs(job.disk)
        except LookupError:
            log.error(
                "Cannot find drive %s, untracking job %s",
                job.disk, job.id)
            self._untrack_job(job.id)
            return

        # Use current top volume size for this extend retry, in case the top
        # volume was extended during the merge.

        try:
            top_size = self._vm.getVolumeSize(
                drive.domainID, drive.poolID, drive.imageID, job.top)
        except errors.StorageUnavailableError as e:
            log.exception(
                "Cannot get top %s size, untracking job %s: %s",
                job.top, job.id, e)
            self._untrack_job(job.id)
            return

        job.extend["top_size"] = top_size.apparentsize
        job.extend["attempt"] += 1

        self._start_extend(drive, job)

    def _extend_completed(self, job_id, error=None):
        """
        Called when extend completed from mailbox worker thread.
        """
        with self._lock:
            try:
                job = self._jobs[job_id]
            except KeyError:
                log.debug("Extend completed after job %s was untracked",
                          job_id)
                return

            if job.state != Job.EXTEND:
                log.debug("Extend completed after job %s switched to state %s",
                          job.id, job.state)
                return

            if error:
                if job.extend["attempt"] < self.EXTEND_ATTEMPTS:
                    log.warning(
                        "Extend %s/%s for job %s failed, retrying: %s",
                        job.extend["attempt"], self.EXTEND_ATTEMPTS, job.id,
                        error)
                    self._retry_extend(job)
                else:
                    log.error(
                        "Extend %s/%s for job %s failed, aborting: %s",
                        job.extend["attempt"], self.EXTEND_ATTEMPTS, job.id,
                        error)
                    self._untrack_job(job.id)
                return

            try:
                drive = self._vm.findDriveByUUIDs(job.disk)
            except LookupError:
                log.error("Cannot find drive %s, untracking job %s",
                          job.disk, job.id)
                self._untrack_job(job.id)
                return

            log.info("Extend completed for job %s, starting commit", job.id)
            job.extend = None
            self._start_commit(drive, job)

    def _create_job(self, job_id, drive, base, top, bandwidth):
        """
        Create new untracked job.
        """
        return Job(
            id=job_id,
            drive=drive.name,
            disk={
                "poolID": drive.poolID,
                "domainID": drive.domainID,
                "imageID": drive.imageID,
                "volumeID": drive.volumeID,
            },
            base=base,
            top=top,
            bandwidth=bandwidth,
        )

    def _get_job(self, drive):
        """
        Must run under self._lock.
        """
        for job in self._jobs.values():
            if all([bool(drive[x] == job.disk[x])
                    for x in ('imageID', 'domainID', 'volumeID')]):
                return job
        raise LookupError("No job found for drive %r" % drive.name)

    def _track_job(self, job, drive):
        """
        Must run under self._lock.
        """
        try:
            existing_job = self._get_job(drive)
        except LookupError:
            self._jobs[job.id] = job
            # Do not persist the job yet. It will be persisted when starting
            # the EXTEND or COMMIT phase.
        else:
            raise JobExistsError(job.id, existing_job.disk["imageID"])

    def _untrack_job(self, job_id):
        """
        Must run under self._lock.
        """
        self._jobs.pop(job_id, None)
        self._cleanup_threads.pop(job_id, None)

        # Successful job modified the volume chain, so we need to sync also
        # disk metadata.
        self._vm.sync_disk_metadata()

        self._persist_jobs()

    def _persist_jobs(self):
        """
        Persist jobs in vm metadata.
        """
        self._vm.sync_jobs_metadata()
        self._vm.sync_metadata()
        self._vm.update_domain_descriptor()

    def find_job_id(self, drive):
        with self._lock:
            for job in self._jobs.values():
                if job.drive == drive:
                    return job.id
        return None

    def load_jobs(self, jobs):
        with self._lock:
            self._jobs = {job_id: Job.from_dict(job_info)
                          for job_id, job_info in jobs.items()}

    def dump_jobs(self):
        with self._lock:
            return {job.id: job.to_dict() for job in self._jobs.values()}

    def has_jobs(self):
        with self._lock:
            return len(self._jobs) > 0

    def query_jobs(self):
        """
        Query tracked jobs and update their status. Returns dict of tracked
        jobs for reporting job status to engine.
        """
        with self._lock:
            for job in list(self._jobs.values()):
                log.debug("Checking job %s", job.id)
                try:
                    if job.state == Job.EXTEND:
                        self._update_extend(job)
                    if job.state == Job.COMMIT:
                        self._update_commit(job)
                    elif job.state == Job.CLEANUP:
                        self._update_cleanup(job)
                except Exception:
                    log.exception("Error updating job %s", job.id)

            return {job.id: job.info() for job in self._jobs.values()}

    def _update_extend(self, job):
        """
        Must run under self._lock.
        """
        duration = time.monotonic() - job.extend["started"]

        if duration > self.EXTEND_TIMEOUT:
            if job.extend["attempt"] < self.EXTEND_ATTEMPTS:
                log.warning(
                    "Extend %s/%s timeout for job %s, retrying",
                    job.extend["attempt"], self.EXTEND_ATTEMPTS, job.id)
                self._retry_extend(job)
            else:
                log.error(
                    "Extend %s/%s timeout for job %s, untracking job",
                    job.extend["attempt"], self.EXTEND_ATTEMPTS, job.id)
                self._untrack_job(job.id)
        else:
            log.debug("Extend for job %s running for %d seconds",
                      job.id, duration)

    def _update_commit(self, job):
        """
        Must run under self._lock.
        """
        try:
            # Returns empty dict if job has gone.
            # pylint: disable=no-member
            job.live_info = self._dom.blockJobInfo(job.drive)
        except libvirt.libvirtError:
            log.exception("Error getting block job info")
            job.live_info = None
            return

        if job.live_info:
            if self._active_commit_ready(job):
                log.info("Job %s is ready for pivot", job.id)
                job.pivot = True
                self._start_cleanup(job)
            else:
                log.debug("Job %s is ongoing", job.id)
        else:
            # Libvirt has stopped reporting this job so we know it will
            # never report it again.
            log.info("Job %s has completed", job.id)
            self._start_cleanup(job)

    def _update_cleanup(self, job):
        """
        Must run under self._lock.
        """
        # If libvirt block jobs has gone, we cannot pivot.
        if job.pivot:
            try:
                # pylint: disable=no-member
                job.pivot = self._dom.blockJobInfo(job.drive) != {}
            except libvirt.libvirtError:
                # We don't know if the job exists, retry later.
                log.exception("Error getting block job info")
                return

        cleanup = self._cleanup_threads.get(job.id)

        # TODO: limit number of pivot retries.

        if not cleanup:
            # Recovery after vdsm restart.
            self._start_cleanup(job)

        elif cleanup.state == CleanupThread.TRYING:
            log.debug("Job %s is ongoing", job.id)

        elif cleanup.state == CleanupThread.FAILED:
            self._start_cleanup(job)

        elif cleanup.state == CleanupThread.DONE:
            log.info("Cleanup completed, untracking job %s", job.id)
            self._untrack_job(job.id)

    def _start_cleanup(self, job):
        """
        Must run under self._lock.
        """
        # Persist the job before starting the cleanup, so vdsm can restart the
        # cleanup after recovery from crash.
        job.state = Job.CLEANUP
        self._persist_jobs()

        try:
            drive = self._vm.findDriveByUUIDs(job.disk)
        except LookupError:
            # Should never happen, and we don't have any good way to handle
            # this.  TODO: Think how to handle this case better.
            log.error("Cannot find drive %s for job %s, retrying later",
                      job.disk, job.id)
            return

        log.info("Starting cleanup for job %s", job.id)
        t = CleanupThread(self._vm, job, drive)
        t.start()
        self._cleanup_threads[job.id] = t

    def _active_commit_ready(self, job):
        # Check the job state in the xml to make sure the job is
        # ready. We know about two interesting corner cases:
        #
        # - cur == 0 and end == 0 when a job starts. Trying to pivot
        #   succeeds, but the xml never updates after that.
        #   See https://bugzilla.redhat.com/1442266.
        #
        # - cur == end and cur != 0, but the job is not ready yet, and
        #   blockJobAbort raises an error.
        #   See https://bugzilla.redhat.com/1376580
        if not job.is_ready():
            return False

        log.debug("Checking xml for drive %r", job.drive)
        # pylint: disable=no-member
        root = ET.fromstring(self._dom.XMLDesc())
        disk_xpath = "./devices/disk/target[@dev='%s'].." % job.drive

        disk = root.find(disk_xpath)
        if disk is None:
            log.warning("Unable to find drive %r in vm xml", job.drive)
            return False

        return disk.find("./mirror[@ready='yes']") is not None

    def wait_for_cleanup(self):
        for t in self._cleanup_threads.values():
            t.join()


class CleanupThread(object):

    # Cleanup states:
    # Starting state for a fresh cleanup thread.
    TRYING = 'TRYING'
    # Cleanup thread failed.
    FAILED = 'FAILED'
    # Cleanup completed successfully.
    DONE = 'DONE'

    # Sample interval for libvirt xml volume chain update after pivot.
    WAIT_INTERVAL = 1

    def __init__(self, vm, job, drive):
        self.vm = vm
        self.job = job
        self.drive = drive
        self.doPivot = job.pivot
        self._state = self.TRYING
        self._thread = concurrent.thread(
            self.run, name="merge/" + job.id[:8])

    @property
    def state(self):
        return self._state

    def start(self):
        self._thread.start()

    def join(self):
        self._thread.join()

    def tryPivot(self):
        # We call imageSyncVolumeChain which will mark the current leaf
        # ILLEGAL.  We do this before requesting a pivot so that we can
        # properly recover the VM in case we crash.  At this point the
        # active layer contains the same data as its parent so the ILLEGAL
        # flag indicates that the VM should be restarted using the parent.
        newVols = [vol['volumeID'] for vol in self.drive.volumeChain
                   if vol['volumeID'] != self.drive.volumeID]
        self.vm.cif.irs.imageSyncVolumeChain(self.drive.domainID,
                                             self.drive.imageID,
                                             self.drive['volumeID'], newVols)

        # A pivot changes the top volume being used for the VM Disk.  Until
        # we can correct our metadata following the pivot we should not
        # attempt to monitor drives.
        # TODO: Stop monitoring only for the live merge disk
        self.vm.drive_monitor.disable()

        self.vm.log.info("Requesting pivot to complete active layer commit "
                         "(job %s)", self.job.id)
        try:
            flags = libvirt.VIR_DOMAIN_BLOCK_JOB_ABORT_PIVOT
            self.vm._dom.blockJobAbort(self.drive.name, flags=flags)
        except libvirt.libvirtError as e:
            self.vm.drive_monitor.enable()
            if e.get_error_code() != libvirt.VIR_ERR_BLOCK_COPY_ACTIVE:
                raise JobPivotError(self.job.id, e)
            raise JobNotReadyError(self.job.id)
        except:
            self.vm.drive_monitor.enable()
            raise

        self._waitForXMLUpdate()
        self.vm.log.info("Pivot completed (job %s)", self.job.id)

    def update_base_size(self):
        # If the drive size was extended just after creating the snapshot which
        # we are removing, the size of the top volume might be larger than the
        # size of the base volume.  In that case libvirt has enlarged the base
        # volume automatically as part of the blockCommit operation.  Update
        # our metadata to reflect this change.
        topVolInfo = self.vm.getVolumeInfo(
            self.drive.domainID,
            self.drive.poolID,
            self.drive.imageID,
            self.job.top)
        self.vm._setVolumeSize(
            self.drive.domainID,
            self.drive.poolID,
            self.drive.imageID,
            self.job.base,
            topVolInfo['capacity'])

    def teardown_top_volume(self):
        ret = self.vm.cif.irs.teardownVolume(
            self.drive.domainID,
            self.drive.imageID,
            self.job.top)
        if ret['status']['code'] != 0:
            raise errors.StorageUnavailableError(
                "Failed to teardown top volume %s" %
                self.job.top)

    @logutils.traceback()
    def run(self):
        try:
            self.update_base_size()
            if self.doPivot:
                self.tryPivot()
            self.vm.log.info("Synchronizing volume chain after live merge "
                             "(job %s)", self.job.id)
            self.vm.sync_volume_chain(self.drive)
            if self.doPivot:
                self.vm.drive_monitor.enable()
            chain_after_merge = [vol['volumeID']
                                 for vol in self.drive.volumeChain]
            if self.job.top not in chain_after_merge:
                self.teardown_top_volume()
            self.vm.log.info("Synchronization completed (job %s)",
                             self.job.id)
            self._setState(self.DONE)
        except JobPivotError as e:
            self.vm.log.warning("%s", e)
            self._setState(self.FAILED)
        except Exception as e:
            self.vm.log.exception(
                "Cleanup error for job %s: %s", self.job.id, e)
            self._setState(self.FAILED)

    def _waitForXMLUpdate(self):
        # Libvirt version 1.2.8-16.el7_1.2 introduced a bug where the
        # synchronous call to blockJobAbort will return before the domain XML
        # has been updated.  This makes it look like the pivot failed when it
        # actually succeeded.  This means that vdsm state will not be properly
        # synchronized and we may start the vm with a stale volume in the
        # future.  See https://bugzilla.redhat.com/show_bug.cgi?id=1202719 for
        # more details.
        # TODO: Remove once we depend on a libvirt with this bug fixed.

        # We expect libvirt to show that the original leaf has been removed
        # from the active volume chain.
        origVols = sorted([x['volumeID'] for x in self.drive.volumeChain])
        expectedVols = origVols[:]
        expectedVols.remove(self.drive.volumeID)

        alias = self.drive['alias']
        self.vm.log.info("Waiting for libvirt to update the XML after pivot "
                         "of drive %s completed", alias)
        while True:
            # This operation should complete in either one or two iterations of
            # this loop.  Until libvirt updates the XML there is nothing to do
            # but wait.  While we wait we continue to tell engine that the job
            # is ongoing.  If we are still in this loop when the VM is powered
            # off, the merge will be resolved manually by engine using the
            # reconcileVolumeChain verb.
            chains = self.vm.drive_get_actual_volume_chain([self.drive])
            if alias not in chains.keys():
                raise RuntimeError("Failed to retrieve volume chain for "
                                   "drive %s.  Pivot failed.", alias)
            curVols = sorted([entry.uuid for entry in chains[alias]])

            if curVols == origVols:
                time.sleep(self.WAIT_INTERVAL)
            elif curVols == expectedVols:
                self.vm.log.info("The XML update has been completed")
                break
            else:
                self.vm.log.error("Bad volume chain found for drive %s. "
                                  "Previous chain: %s, Expected chain: %s, "
                                  "Actual chain: %s", alias, origVols,
                                  expectedVols, curVols)
                raise RuntimeError("Bad volume chain found")

    def _setState(self, state):
        self.vm.log.debug("Switching state from %r to %r (job: %s)",
                          self._state, state, self.job.id)
        self._state = state
