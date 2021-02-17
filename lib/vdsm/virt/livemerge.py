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


class JobNotReadyError(errors.Base):
    msg = "Job {self.job_id} is not ready for commit"

    def __init__(self, job_id):
        self.job_id = job_id


class JobUnrecoverableError(errors.Base):
    msg = "Job {self.job_id} failed with libvirt error: {self.reason}"

    def __init__(self, job_id, reason):
        self.job_id = job_id
        self.reason = reason

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

    def __init__(self, id, drive, disk, top, base, bandwidth, gone=False):
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

        # Set when libvirt stopped reporting this job.
        self.gone = gone

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

    def is_ready(self):
        """
        Return True if this is an active commit, and the job finished the first
        phase and ready to pivot. Note that even if the job is ready, we need
        to check the job status in the vm xml.
        """
        return (self.active_commit and
                self.live_info and
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
            "gone": self.gone,
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
            gone=d["gone"],
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

        job = Job(
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

        try:
            base_info = self._vm.getVolumeInfo(
                drive.domainID, drive.poolID, drive.imageID, job.base)
            top_info = self._vm.getVolumeInfo(
                drive.domainID, drive.poolID, drive.imageID, job.top)
        except errors.StorageUnavailableError as e:
            raise exception.MergeFailed(
                str(e), top=top, base=job.base, job=job_id)

        # If base is a shared volume then we cannot allow a merge.  Otherwise
        # We'd corrupt the shared volume for other users.
        if base_info['voltype'] == 'SHARED':
            raise exception.MergeFailed(
                "Base volume is shared", base_info=base_info, job=job_id)

        # Make sure we can merge into the base in case the drive was enlarged.
        self._validate_base_size(drive, base_info, top_info)

        if self._base_needs_refresh(drive, base_info):
            self._refresh_base(drive, base_info)

        self._start_commit(drive, job)

        if self._base_needs_extend(drive, base_info):
            self._start_extend(drive, job, base_info, top_info)

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

        self._vm.refreshDriveVolume({
            'domainID': drive.domainID,
            'imageID': drive.imageID,
            'name': drive.name,
            'poolID': drive.poolID,
            'volumeID': base_info['uuid'],
        })

    def _start_commit(self, drive, job):
        """
        Start libvirt blockCommit block job.
        """
        # Check that libvirt exposes full volume chain information
        chains = self._vm.drive_get_actual_volume_chain([drive])
        if drive['alias'] not in chains:
            raise exception.MergeFailed(
                "Libvirt does not support volume chain monitoring",
                drive=drive, alias=drive["alias"], chains=chains, job=job.id)

        actual_chain = chains[drive['alias']]
        try:
            base_target = drive.volume_target(job.base, actual_chain)
            top_target = drive.volume_target(job.top, actual_chain)
        except VolumeNotFound as e:
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

        # Take the jobs lock here to protect the new job we are tracking from
        # being cleaned up by query_jobs() since it won't exist right away
        with self._lock:
            try:
                self._track_job(job, drive)
            except JobExistsError as e:
                raise exception.MergeFailed(str(e), job=job.id)

            orig_chain = [entry.uuid for entry in actual_chain]
            chain_str = logutils.volume_chain_to_str(orig_chain)
            log.info("Starting merge with job_id=%r, original chain=%s, "
                     "disk=%r, base=%r, top=%r, bandwidth=%d, flags=%d",
                     job.id, chain_str, drive.name, base_target,
                     top_target, job.bandwidth, flags)

            try:
                # pylint: disable=no-member
                self._dom.blockCommit(
                    drive.name, base_target, top_target, job.bandwidth, flags)
            except libvirt.libvirtError as e:
                self._untrack_job(job.id)
                raise exception.MergeFailed(str(e), job=job.id)

    def _base_needs_extend(self, drive, base_info):
        # blockCommit will cause data to be written into the base volume.
        # Perform an initial extension to ensure there is enough space to
        # copy all the required data.  Normally we'd use monitoring to extend
        # the volume on-demand but internal watermark information is not being
        # reported by libvirt so we must do the full extension up front.  In
        # the worst case, the allocated size of 'base' should be increased by
        # the allocated size of 'top' plus one additional chunk to accomodate
        # additional writes to 'top' during the live merge operation.
        return drive.chunked and base_info['format'] == 'COW'

    def _start_extend(self, drive, job, base_info, top_info):
        """
        Start extend operation for the base volume.
        """
        capacity, alloc, physical = self._vm.getExtendInfo(drive)
        base_size = int(base_info['apparentsize'])
        top_size = int(top_info['apparentsize'])
        max_alloc = base_size + top_size

        log.info("Starting extend for job=%s drive=%s volume=%s size=%s",
                 job.id, drive.name, job.base, max_alloc)
        self._vm.extendDriveVolume(
            drive, job.base, max_alloc, capacity)

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
        else:
            raise JobExistsError(job.id, existing_job.disk["imageID"])

        self._vm.sync_jobs_metadata()
        self._vm.sync_metadata()
        self._vm.update_domain_descriptor()

    def _untrack_job(self, job_id):
        """
        Must run under self._lock.
        """
        # If there was contention on self._lock, this may have already been
        # removed
        self._jobs.pop(job_id, None)

        self._vm.sync_disk_metadata()
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
        jobs dict, for reporting job status to engine.
        """
        tracked_jobs = {}

        # We need to take the jobs lock here to ensure that we don't race with
        # another call to merge() where the job has been recorded but not yet
        # started.
        with self._lock:
            for job in list(self._jobs.values()):
                log.debug("Checking job %s", job.id)

                # Handle successful jobs early because the job just needs
                # to be untracked and the stored disk info might be stale
                # anyway (ie. after active layer commit).
                cleanThread = self._cleanup_threads.get(job.id)
                if (cleanThread
                        and cleanThread.state == CleanupThread.DONE):
                    log.info("Cleanup thread %s successfully completed, "
                             "untracking job %s (base=%s, top=%s)",
                             cleanThread, job.id,
                             job.base,
                             job.top)
                    self._untrack_job(job.id)
                    continue

                try:
                    drive = self._vm.findDriveByUUIDs(job.disk)
                except LookupError:
                    # Drive loopkup may fail only in case of active layer
                    # merge, and pivot completed.
                    disk = job.disk
                    if disk["volumeID"] != job.top:
                        log.error("Cannot find drive for job %s (disk=%s)",
                                  job.id, job.disk)
                        # TODO: Should we report this job?
                        continue

                    # Active layer merge, check if pivot completed.
                    pivoted_drive = dict(disk)
                    pivoted_drive["volumeID"] = job.base
                    try:
                        drive = self._vm.findDriveByUUIDs(pivoted_drive)
                    except LookupError:
                        log.error("Pivot completed but cannot find drive "
                                  "for job %s (disk=%s)",
                                  job.id, pivoted_drive)
                        # TODO: Should we report this job?
                        continue

                if not job.gone:
                    try:
                        # If the job is found we get a dict with job info. If
                        # the job does not exists we get an empty dict.
                        # pylint: disable=no-member
                        job.live_info = self._dom.blockJobInfo(drive.name, 0)
                    except libvirt.libvirtError:
                        log.exception("Error getting block job info")
                        job.live_info = None
                        tracked_jobs[job.id] = job.info()
                        continue

                if job.live_info:
                    doPivot = self._active_commit_ready(job, drive)
                else:
                    # Libvirt has stopped reporting this job so we know it will
                    # never report it again.
                    if not job.gone:
                        log.info("Libvirt job %s was terminated", job.id)
                    job.gone = True
                    doPivot = False

                if not job.live_info or doPivot:
                    if not cleanThread:
                        # There is no cleanup thread so the job must have just
                        # ended.  Spawn an async cleanup.
                        log.info("Starting cleanup thread for job: %s",
                                 job.id)
                        self._start_cleanup_thread(job, drive, doPivot)
                    elif cleanThread.state == CleanupThread.TRYING:
                        # Let previously started cleanup thread continue
                        log.debug("Still waiting for job %s to be "
                                  "synchronized", job.id)
                    elif cleanThread.state == CleanupThread.RETRY:
                        log.info("Previous job %s cleanup thread failed with "
                                 "recoverable error, retrying",
                                 job.id)
                        self._start_cleanup_thread(job, drive, doPivot)
                    elif cleanThread.state == CleanupThread.ABORT:
                        log.error("Aborting job %s due to an unrecoverable "
                                  "error", job.id)
                        self._untrack_job(job.id)
                        continue

                tracked_jobs[job.id] = job.info()

        return tracked_jobs

    def _start_cleanup_thread(self, job, drive, needPivot):
        """
        Must be caller when holding self._lock.
        """
        t = CleanupThread(self._vm, job, drive, needPivot)
        t.start()
        self._cleanup_threads[job.id] = t

    def _active_commit_ready(self, job, drive):
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

        log.debug("Checking xml for drive %r", drive.name)
        # pylint: disable=no-member
        root = ET.fromstring(self._dom.XMLDesc(0))
        disk_xpath = "./devices/disk/target[@dev='%s'].." % drive.name

        disk = root.find(disk_xpath)
        if disk is None:
            log.warning("Unable to find %r in vm xml", drive)
            return False

        return disk.find("./mirror[@ready='yes']") is not None

    def wait_for_cleanup(self):
        for t in self._cleanup_threads.values():
            t.join()


class CleanupThread(object):

    # Cleanup states:
    # Starting state for a fresh cleanup thread.
    TRYING = 'TRYING'
    # Cleanup thread failed with recoverable error, the caller should
    # retry the cleanup.
    RETRY = 'RETRY'
    # Cleanup completed successfully.
    DONE = 'DONE'
    # Unrecoverable cleanup error, run should not be retried by the caller.
    ABORT = 'ABORT'

    # Sample interval for libvirt xml volume chain update after pivot.
    WAIT_INTERVAL = 1

    def __init__(self, vm, job, drive, doPivot):
        self.vm = vm
        self.job = job
        self.drive = drive
        self.doPivot = doPivot
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
            self.vm._dom.blockJobAbort(self.drive.name, flags)
        except libvirt.libvirtError as e:
            self.vm.drive_monitor.enable()
            if e.get_error_code() != libvirt.VIR_ERR_BLOCK_COPY_ACTIVE:
                raise JobUnrecoverableError(self.job.id, e)
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
        except JobNotReadyError as e:
            self.vm.log.warning("Pivot failed (job: %s): %s, retrying later",
                                self.job.id, e)
            self._setState(self.RETRY)
        except JobUnrecoverableError as e:
            self.vm.log.exception("Pivot failed (job: %s): %s, aborting due "
                                  "to an unrecoverable error",
                                  self.job.id, e)
            self._setState(self.ABORT)
        except Exception as e:
            self.vm.log.exception("Cleanup failed with recoverable error "
                                  "(job: %s): %s", self.job.id, e)
            self._setState(self.RETRY)

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
