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
from vdsm.common import logutils
from vdsm.common import response
from vdsm.common.define import doneCode, errCode

from vdsm.virt import errors
from vdsm.virt import virdomain
from vdsm.virt.vmdevices.storage import DISK_TYPE, VolumeNotFound

import libvirt


class BlockJobExistsError(errors.Base):
    msg = "Block job already exists"


class BlockCopyActiveError(errors.Base):
    msg = "Block copy job {self.job_id} is not ready for commit"

    def __init__(self, job_id):
        self.job_id = job_id


class BlockJobUnrecoverableError(errors.Base):
    msg = "Block job {self.job_id} failed with libvirt error: {self.reason}"

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


class DriveMerger:

    def __init__(self, vm):
        self._vm = vm
        self._jobsLock = threading.RLock()
        self._blockJobs = {}
        self._liveMergeCleanupThreads = {}
        self._dom = DomainAdapter(vm)

    def merge(self, driveSpec, baseVolUUID, topVolUUID, bandwidth, jobUUID):
        bandwidth = int(bandwidth)
        if jobUUID is None:
            jobUUID = str(uuid.uuid4())

        try:
            drive = self._vm.findDriveByUUIDs(driveSpec)
        except LookupError:
            return response.error('imageErr')

        # Check that libvirt exposes full volume chain information
        chains = self._vm.drive_get_actual_volume_chain([drive])
        if drive['alias'] not in chains:
            log.error("merge: libvirt does not support volume chain "
                      "monitoring.  Unable to perform live merge. "
                      "drive: %s, alias: %s, chains: %r",
                      drive.name, drive['alias'], chains)
            return response.error('mergeErr')

        actual_chain = chains[drive['alias']]

        try:
            base_target = drive.volume_target(baseVolUUID, actual_chain)
            top_target = drive.volume_target(topVolUUID, actual_chain)
        except VolumeNotFound as e:
            log.error("merge: %s", e)
            return response.error('mergeErr')

        try:
            baseInfo = self._vm.getVolumeInfo(drive.domainID, drive.poolID,
                                              drive.imageID, baseVolUUID)
            topInfo = self._vm.getVolumeInfo(drive.domainID, drive.poolID,
                                             drive.imageID, topVolUUID)
        except errors.StorageUnavailableError:
            log.error("Unable to get volume information")
            return errCode['mergeErr']

        # If base is a shared volume then we cannot allow a merge.  Otherwise
        # We'd corrupt the shared volume for other users.
        if baseInfo['voltype'] == 'SHARED':
            log.error("Refusing to merge into a shared volume")
            return errCode['mergeErr']

        # Indicate that we expect libvirt to maintain the relative paths of
        # backing files.  This is necessary to ensure that a volume chain is
        # visible from any host even if the mountpoint is different.
        flags = libvirt.VIR_DOMAIN_BLOCK_COMMIT_RELATIVE

        if topVolUUID == drive.volumeID:
            # Pass a flag to libvirt to indicate that we expect a two phase
            # block job.  In the first phase, data is copied to base.  Once
            # completed, an event is raised to indicate that the job has
            # transitioned to the second phase.  We must then tell libvirt to
            # pivot to the new active layer (baseVolUUID).
            flags |= libvirt.VIR_DOMAIN_BLOCK_COMMIT_ACTIVE

        # Make sure we can merge into the base in case the drive was enlarged.
        if not self._can_merge_into(drive, baseInfo, topInfo):
            return errCode['destVolumeTooSmall']

        # If the base volume format is RAW and its size is smaller than its
        # capacity (this could happen because the engine extended the base
        # volume), we have to refresh the volume to cause lvm to get current lv
        # size from storage, and update the kernel so the lv reflects the real
        # size on storage. Not refreshing the volume may fail live merge.
        # This could happen if disk extended after taking a snapshot but before
        # performing the live merge.  See https://bugzilla.redhat.com/1367281
        if (drive.chunked
                and baseInfo['format'] == 'RAW'
                and int(baseInfo['apparentsize']) < int(baseInfo['capacity'])):
            log.info("Refreshing raw volume %r (apparentsize=%s, "
                     "capacity=%s)",
                     baseVolUUID, baseInfo['apparentsize'],
                     baseInfo['capacity'])
            self._vm.refreshDriveVolume({
                'domainID': drive.domainID, 'poolID': drive.poolID,
                'imageID': drive.imageID, 'volumeID': baseVolUUID,
                'name': drive.name,
            })

        # Take the jobs lock here to protect the new job we are tracking from
        # being cleaned up by queryBlockJobs() since it won't exist right away
        with self._jobsLock:
            try:
                self._track_block_job(jobUUID, drive, baseVolUUID, topVolUUID,
                                      'commit')
            except BlockJobExistsError:
                log.error("A block job is already active on this disk")
                return response.error('mergeErr')

            orig_chain = [entry.uuid for entry in chains[drive['alias']]]
            chain_str = logutils.volume_chain_to_str(orig_chain)
            log.info("Starting merge with jobUUID=%r, original chain=%s, "
                     "disk=%r, base=%r, top=%r, bandwidth=%d, flags=%d",
                     jobUUID, chain_str, drive.name, base_target,
                     top_target, bandwidth, flags)

            try:
                # pylint: disable=no-member
                self._dom.blockCommit(
                    drive.name, base_target, top_target, bandwidth, flags)
            except libvirt.libvirtError:
                log.exception("Live merge failed (job: %s)", jobUUID)
                self._untrack_block_job(jobUUID)
                return response.error('mergeErr')

        # blockCommit will cause data to be written into the base volume.
        # Perform an initial extension to ensure there is enough space to
        # copy all the required data.  Normally we'd use monitoring to extend
        # the volume on-demand but internal watermark information is not being
        # reported by libvirt so we must do the full extension up front.  In
        # the worst case, the allocated size of 'base' should be increased by
        # the allocated size of 'top' plus one additional chunk to accomodate
        # additional writes to 'top' during the live merge operation.
        if drive.chunked and baseInfo['format'] == 'COW':
            capacity, alloc, physical = self._vm.getExtendInfo(drive)
            baseSize = int(baseInfo['apparentsize'])
            topSize = int(topInfo['apparentsize'])
            maxAlloc = baseSize + topSize
            self._vm.extendDriveVolume(drive, baseVolUUID, maxAlloc, capacity)

        # Trigger the collection of stats before returning so that callers
        # of getVmStats after this returns will see the new job
        self._vm.updateVmJobs()

        return {'status': doneCode}

    def _can_merge_into(self, drive, base_info, top_info):
        # If the drive was resized the top volume could be larger than the
        # base volume.  Libvirt can handle this situation for file-based
        # volumes and block qcow volumes (where extension happens dynamically).
        # Raw block volumes cannot be extended by libvirt so we require ovirt
        # engine to extend them before calling merge.  Check here.
        if drive.diskType != DISK_TYPE.BLOCK or base_info['format'] != 'RAW':
            return True

        if int(base_info['capacity']) < int(top_info['capacity']):
            log.warning("The base volume is undersized and cannot be "
                        "extended (base capacity: %s, top capacity: %s)",
                        base_info['capacity'], top_info['capacity'])
            return False
        return True

    def _get_block_job(self, drive):
        """
        Must run under jobsLock.
        """
        for job in self._blockJobs.values():
            if all([bool(drive[x] == job['disk'][x])
                    for x in ('imageID', 'domainID', 'volumeID')]):
                return job
        raise LookupError("No block job found for drive %r" % drive.name)

    def _track_block_job(self, jobID, drive, base, top, strategy):
        """
        Must run under jobsLock.
        """
        driveSpec = dict((k, drive[k]) for k in
                         ('poolID', 'domainID', 'imageID', 'volumeID'))
        try:
            job = self._get_block_job(drive)
        except LookupError:
            newJob = {'jobID': jobID, 'disk': driveSpec,
                      'baseVolume': base, 'topVolume': top,
                      'strategy': strategy, 'blockJobType': 'commit',
                      'drive': drive.name}
            self._blockJobs[jobID] = newJob
        else:
            log.error("Cannot add block job %s.  A block job with id "
                      "%s already exists for image %s", jobID,
                      job['jobID'], drive['imageID'])
            raise BlockJobExistsError()

        self._vm.sync_block_job_info()
        self._vm.sync_metadata()
        self._vm.update_domain_descriptor()

    def _untrack_block_job(self, jobID):
        """
        Must run under jobsLock.
        """
        # If there was contention on the jobsLock, this may have
        # already been removed
        self._blockJobs.pop(jobID, None)

        self._vm.sync_disk_metadata()
        self._vm.sync_block_job_info()
        self._vm.sync_metadata()
        self._vm.update_domain_descriptor()

    def job_id(self, drive):
        with self._jobsLock:
            for job in self._blockJobs.values():
                if job['drive'] == drive:
                    return job['jobID']
        return None

    def load_jobs(self, jobs):
        self._blockJobs = jobs

    def dump_jobs(self):
        with self._jobsLock:
            return self._blockJobs.copy()

    def has_jobs(self):
        with self._jobsLock:
            return len(self._blockJobs) > 0

    def queryBlockJobs(self):
        jobsRet = {}
        # We need to take the jobs lock here to ensure that we don't race with
        # another call to merge() where the job has been recorded but not yet
        # started.
        with self._jobsLock:
            for storedJob in list(self._blockJobs.values()):
                jobID = storedJob['jobID']
                log.debug("Checking job %s", jobID)

                # Handle successful jobs early because the job just needs
                # to be untracked and the stored disk info might be stale
                # anyway (ie. after active layer commit).
                cleanThread = self._liveMergeCleanupThreads.get(jobID)
                if (cleanThread
                        and cleanThread.state == LiveMergeCleanupThread.DONE):
                    log.info("Cleanup thread %s successfully completed, "
                             "untracking job %s (base=%s, top=%s)",
                             cleanThread, jobID,
                             storedJob["baseVolume"],
                             storedJob["topVolume"])
                    self._untrack_block_job(jobID)
                    continue

                try:
                    drive = self._vm.findDriveByUUIDs(storedJob['disk'])
                except LookupError:
                    # Drive loopkup may fail only in case of active layer
                    # merge, and pivot completed.
                    disk = storedJob['disk']
                    if disk["volumeID"] != storedJob["topVolume"]:
                        log.error("Cannot find drive for job %s (disk=%s)",
                                  jobID, storedJob['disk'])
                        continue

                    # Active layer merge, check if pivot completed.
                    pivoted_drive = dict(disk)
                    pivoted_drive["volumeID"] = storedJob["baseVolume"]
                    try:
                        drive = self._vm.findDriveByUUIDs(pivoted_drive)
                    except LookupError:
                        log.error("Pivot completed but cannot find drive "
                                  "for job %s (disk=%s)",
                                  jobID, pivoted_drive)
                        continue

                entry = {'id': jobID, 'jobType': 'block',
                         'blockJobType': storedJob['blockJobType'],
                         'bandwidth': 0, 'cur': '0', 'end': '0',
                         'imgUUID': storedJob['disk']['imageID'],
                         'drive': storedJob['drive']}

                liveInfo = None
                if 'gone' not in storedJob:
                    try:
                        # pylint: disable=no-member
                        liveInfo = self._dom.blockJobInfo(drive.name, 0)
                    except libvirt.libvirtError:
                        log.exception("Error getting block job info")
                        jobsRet[jobID] = entry
                        continue

                if liveInfo:
                    log.debug("Job %s live info: %s", jobID, liveInfo)
                    entry['bandwidth'] = liveInfo['bandwidth']
                    entry['cur'] = str(liveInfo['cur'])
                    entry['end'] = str(liveInfo['end'])
                    doPivot = self._activeLayerCommitReady(liveInfo, drive)
                else:
                    # Libvirt has stopped reporting this job so we know it will
                    # never report it again.
                    if 'gone' not in storedJob:
                        log.info("Libvirt job %s was terminated", jobID)
                    storedJob['gone'] = True
                    doPivot = False

                if not liveInfo or doPivot:
                    if not cleanThread:
                        # There is no cleanup thread so the job must have just
                        # ended.  Spawn an async cleanup.
                        log.info("Starting cleanup thread for job: %s",
                                 jobID)
                        self._start_cleanup_thread(storedJob, drive, doPivot)
                    elif cleanThread.state == LiveMergeCleanupThread.TRYING:
                        # Let previously started cleanup thread continue
                        log.debug("Still waiting for block job %s to be "
                                  "synchronized", jobID)
                    elif cleanThread.state == LiveMergeCleanupThread.RETRY:
                        log.info("Previous job %s cleanup thread failed with "
                                 "recoverable error, retrying",
                                 jobID)
                        self._start_cleanup_thread(storedJob, drive, doPivot)
                    elif cleanThread.state == LiveMergeCleanupThread.ABORT:
                        log.error("Aborting job %s due to an unrecoverable "
                                  "error", jobID)
                        self._untrack_block_job(jobID)
                        # Don't report job as progressing in returned jobs.
                        continue

                jobsRet[jobID] = entry

        return jobsRet

    def _start_cleanup_thread(self, job, drive, needPivot):
        """
        Must be caller when holding self._jobsLock.
        """
        t = LiveMergeCleanupThread(self._vm, job, drive, needPivot)
        t.start()
        self._liveMergeCleanupThreads[job['jobID']] = t

    def _activeLayerCommitReady(self, jobInfo, drive):
        try:
            pivot = libvirt.VIR_DOMAIN_BLOCK_JOB_TYPE_ACTIVE_COMMIT
        except AttributeError:
            return False
        if (jobInfo['cur'] == jobInfo['end'] and jobInfo['type'] == pivot):

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

            log.debug("Checking xml for drive %r", drive.name)
            # pylint: disable=no-member
            root = ET.fromstring(self._dom.XMLDesc(0))
            disk_xpath = "./devices/disk/target[@dev='%s'].." % drive.name
            disk = root.find(disk_xpath)
            if disk is None:
                log.warning("Unable to find %r in vm xml", drive)
                return False
            return disk.find("./mirror[@ready='yes']") is not None
        return False

    def wait_for_cleanup(self):
        for t in self._liveMergeCleanupThreads.values():
            t.join()


class LiveMergeCleanupThread(object):

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
            self.run, name="merge/" + job["jobID"][:8])

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
                         "(job %s)", self.job['jobID'])
        try:
            flags = libvirt.VIR_DOMAIN_BLOCK_JOB_ABORT_PIVOT
            self.vm._dom.blockJobAbort(self.drive.name, flags)
        except libvirt.libvirtError as e:
            self.vm.drive_monitor.enable()
            if e.get_error_code() != libvirt.VIR_ERR_BLOCK_COPY_ACTIVE:
                raise BlockJobUnrecoverableError(self.job['jobID'], e)
            raise BlockCopyActiveError(self.job['jobID'])
        except:
            self.vm.drive_monitor.enable()
            raise

        self._waitForXMLUpdate()
        self.vm.log.info("Pivot completed (job %s)", self.job['jobID'])

    def update_base_size(self):
        # If the drive size was extended just after creating the snapshot which
        # we are removing, the size of the top volume might be larger than the
        # size of the base volume.  In that case libvirt has enlarged the base
        # volume automatically as part of the blockCommit operation.  Update
        # our metadata to reflect this change.
        topVolUUID = self.job['topVolume']
        baseVolUUID = self.job['baseVolume']
        topVolInfo = self.vm.getVolumeInfo(self.drive.domainID,
                                           self.drive.poolID,
                                           self.drive.imageID, topVolUUID)
        self.vm._setVolumeSize(self.drive.domainID, self.drive.poolID,
                               self.drive.imageID, baseVolUUID,
                               topVolInfo['capacity'])

    def teardown_top_volume(self):
        ret = self.vm.cif.irs.teardownVolume(
            self.drive.domainID,
            self.drive.imageID,
            self.job['topVolume'])
        if ret['status']['code'] != 0:
            raise errors.StorageUnavailableError(
                "Failed to teardown top volume %s" %
                self.job['topVolume'])

    @logutils.traceback()
    def run(self):
        try:
            self.update_base_size()
            if self.doPivot:
                self.tryPivot()
            self.vm.log.info("Synchronizing volume chain after live merge "
                             "(job %s)", self.job['jobID'])
            self.vm.sync_volume_chain(self.drive)
            if self.doPivot:
                self.vm.drive_monitor.enable()
            chain_after_merge = [vol['volumeID']
                                 for vol in self.drive.volumeChain]
            if self.job['topVolume'] not in chain_after_merge:
                self.teardown_top_volume()
            self.vm.log.info("Synchronization completed (job %s)",
                             self.job['jobID'])
            self._setState(self.DONE)
        except BlockCopyActiveError as e:
            self.vm.log.warning("Pivot failed (job: %s): %s, retrying later",
                                self.job['jobID'], e)
            self._setState(self.RETRY)
        except BlockJobUnrecoverableError as e:
            self.vm.log.exception("Pivot failed (job: %s): %s, aborting due "
                                  "to an unrecoverable error",
                                  self.job['jobID'], e)
            self._setState(self.ABORT)
        except Exception as e:
            self.vm.log.exception("Cleanup failed with recoverable error "
                                  "(job: %s): %s", self.job['jobID'], e)
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
                          self._state, state, self.job['jobID'])
        self._state = state
