#
# Copyright 2017 Red Hat, Inc.
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

"""
This module implements a job that creates a snapshot for a given VM.
"""

from __future__ import absolute_import

import os
import threading
import time

import libvirt

from vdsm.common import exception
from vdsm.common import logutils
from vdsm.common import response
from vdsm import utils
from vdsm.config import config
from vdsm.common import concurrent
from vdsm.common import properties, xmlutils
from vdsm.common.compat import pickle
from vdsm.common.time import monotonic_time

# TODO: remove these imports, code using this should use storage apis.
from vdsm.storage import outOfProcess as oop
from vdsm.storage import sd

from vdsm.virt import vmstatus
from vdsm.virt import vmxml
from vdsm.virt import vmdevices
from vdsm.virt.vmdevices import lookup
from vdsm.virt.vmdevices import storagexml
import vdsm.virt.jobs
import vdsm.virt.vm


def write_snapshot_md(vm, snapshot_data, lock):
    with lock:
        try:
            vm.update_snapshot_metadata(snapshot_data)
        except libvirt.libvirtError as e:
            vm.log.error("Couldn't save metadata %s", e)


def read_snapshot_md(vm, lock):
    with lock:
        return vm.snapshot_metadata()


def set_abort(vm, snapshot_data, completed, abort, lock):
    with lock:
        if not completed.is_set():
            abort.set()
            snapshot_data['abort'] = abort.is_set()
    if abort.is_set():
        write_snapshot_md(vm, snapshot_data, lock)


def set_completed(vm, snapshot_data, completed, abort, lock):
    with lock:
        if abort.is_set():
            vm.log.info("Snapshot timeout reached, operation aborted")
        else:
            completed.set()
            snapshot_data["completed"] = completed.is_set()


class Job(vdsm.virt.jobs.Job):

    def __init__(self, vm, snap_drives, memory_params,
                 frozen, job_uuid, recovery=False, timeout=30):
        super(Job, self).__init__(job_uuid, 'snapshot_vm')
        self._vm = vm
        self._snap_drives = snap_drives
        self._memory_params = memory_params
        self._frozen = frozen
        self._job_uuid = job_uuid
        self._recovery = recovery
        # Converting the timeout in minutes to seconds
        self._timeout = timeout * 60
        self._abort = threading.Event()
        self._completed = threading.Event()
        self._lock = threading.Lock()
        self._snapshot_job = read_snapshot_md(self._vm, self._lock)
        self._load_metadata()

    def _run(self):
        t = AbortSnapshot(self._vm, self._job_uuid, self._start_time,
                          self._timeout, self._abort, self._completed,
                          self._snapshot_job, self._lock)
        t.start()
        try:
            if self._recovery:
                LiveSnapshotRecovery(self._vm, self._abort, self._completed,
                                     self._snapshot_job, self._lock).run()
            else:
                snap = Snapshot(self._vm, self._snap_drives,
                                self._memory_params, self._frozen,
                                self._job_uuid, self._abort, self._completed,
                                self._start_time, self._timeout,
                                self._snapshot_job, self._lock)
                snap.snapshot()
        finally:
            t.join()

    def _load_metadata(self):
        # If self._snapshot_job is not None, then it was already populated
        # with some data. This means, we are in recovery.
        # The data is taken to continue the recovery and abort.
        if self._snapshot_job:
            self._start_time = float(self._snapshot_job['startTime'])
            self._timeout = int(self._snapshot_job['timeout'])
            if self._snapshot_job['abort']:
                self._abort.set()
            if self._snapshot_job['completed']:
                self._completed.set()
        else:
            self._snapshot_job = {}
            self._start_time = monotonic_time()


class Snapshot(properties.Owner):
    _job_uuid = properties.UUID(required=True)

    def __init__(self, vm, snap_drives, memory_params, frozen, job_uuid,
                 abort, completed, start_time, timeout, snapshot_job, lock):
        self._vm = vm
        self._snap_drives = snap_drives
        self._memory_params = memory_params
        self._frozen = frozen
        self._job_uuid = job_uuid
        self._abort = abort
        self._completed = completed
        # When creating memory snapshot libvirt will pause the vm
        self._should_freeze = not (self._memory_params or self._frozen)
        self._start_time = start_time
        self._timeout = timeout
        self._snapshot_job = snapshot_job
        self._lock = lock
        self._init_snapshot_metadata()

    def _init_snapshot_metadata(self):
        # If _snapshot_job is populated, we loaded it in Job class.
        # We can skip it since we are passing the values from Job to Snapshot.
        if not self._snapshot_job:
            # This branch means self._snapshot_job is empty. Therefore,
            # we are starting the job in regular flow. The data is needed
            # for the snapshot job and the abort thread.
            # Initializing the job parameters; 'abort' and 'completed'
            # will be changed once the job status changes.
            self._snapshot_job.update({'startTime': str(self._start_time),
                                       'timeout': str(self._timeout),
                                       'abort': False,
                                       'completed': False,
                                       "jobUUID": self._job_uuid,
                                       "frozen": self._frozen,
                                       "memoryParams": self._memory_params})
            write_snapshot_md(self._vm, self._snapshot_job, self._lock)

    def _thaw_vm(self):
        # Must always thaw, even if freeze failed; in case the guest
        # did freeze the filesystems, but failed to reply in time.
        # Libvirt is using same logic (see src/qemu/qemu_driver.c).
        if self._should_freeze:
            self._vm.thaw()

    def finalize_vm(self, memory_vol):
        try:
            if self._abort.is_set():
                self._thaw_vm()
            self._vm.drive_monitor.enable()
            if self._memory_params:
                self._vm.cif.teardownVolumePath(memory_vol)
            if config.getboolean('vars', 'time_sync_snapshot_enable'):
                self._vm.syncGuestTime()
        finally:
            # Cleaning snapshot job metadata
            self._snapshot_job = None
            write_snapshot_md(self._vm, self._snapshot_job, self._lock)
            if self._abort.is_set():
                raise exception.ActionStopped()

    def teardown(self, memory_vol_path, memory_vol, new_drives, vm_drives):
        if self._abort.is_set():
            self.finalize_vm(memory_vol)
            return False

        self._vm.log.info('Starting snapshot teardown')
        result = True

        def pad_memory_volume(memory_vol_path, sd_uuid):
            sd_type = sd.name2type(
                self._vm.cif.irs.getStorageDomainInfo(sd_uuid)['info']['type'])
            if sd_type in sd.FILE_DOMAIN_TYPES:
                iop = oop.getProcessPool(sd_uuid)
                iop.fileUtils.padToBlockSize(memory_vol_path)

        try:
            self._thaw_vm()

            # We are padding the memory volume with block size of zeroes
            # because qemu-img truncates files such that their size is
            # round down to the closest multiple of block size (bz 970559).
            # This code should be removed once qemu-img will handle files
            # with size that is not multiple of block size correctly.
            if self._memory_params:
                pad_memory_volume(memory_vol_path, memory_vol['domainID'])

            for drive in new_drives.values():
                # Update the drive information
                _, old_volume_id = vm_drives[drive["name"]]
                try:
                    self._vm.updateDriveParameters(drive)
                except Exception:
                    # Here it's too late to fail, the switch already happened
                    # and there's nothing we can do, we must to proceed anyway
                    # to report the live snapshot success.
                    self._vm.log.exception("Failed to update drive information"
                                           " for '%s'", drive)

                drive_obj = lookup.drive_by_name(
                    self._vm.getDiskDevices()[:], drive["name"])
                self._vm.clear_drive_threshold(drive_obj, old_volume_id)

                try:
                    self._vm.updateDriveVolume(drive_obj)
                except vdsm.virt.vm.StorageUnavailableError as e:
                    # Will be recovered on the next monitoring cycle
                    self._vm.log.error("Unable to update drive %r "
                                       "volume size: %s", drive["name"], e)
        except Exception as e:
            self._vm.log.error("Snapshot teardown error: %s, "
                               "trying to continue teardown", e)
            result = False
        finally:
            self.finalize_vm(memory_vol)
        return result

    def __repr__(self):
        return ("<%s vm=%s job=%s 0x%s>" %
                (self.__class__.__name__, self._vm.id,
                 self._job_uuid, id(self)))

    def snapshot(self):
        """Live snapshot command"""

        def norm_snap_drive_params(drive):
            """Normalize snapshot parameters"""

            if "baseVolumeID" in drive:
                base_drv = {"device": "disk",
                            "domainID": drive["domainID"],
                            "imageID": drive["imageID"],
                            "volumeID": drive["baseVolumeID"]}
                target_drv = base_drv.copy()
                target_drv["volumeID"] = drive["volumeID"]

            elif "baseGUID" in drive:
                base_drv = {"GUID": drive["baseGUID"]}
                target_drv = {"GUID": drive["GUID"]}

            elif "baseUUID" in drive:
                base_drv = {"UUID": drive["baseUUID"]}
                target_drv = {"UUID": drive["UUID"]}

            else:
                base_drv, target_drv = (None, None)

            return base_drv, target_drv

        def rollback_drives(new_drives):
            """Rollback the prepared volumes for the snapshot"""

            for vm_dev_name, drive in new_drives.items():
                try:
                    self._vm.cif.teardownVolumePath(drive)
                except Exception:
                    self._vm.log.exception("Unable to teardown drive: %s",
                                           vm_dev_name)

        def memory_snapshot(memory_volume_path):
            """Libvirt snapshot XML"""

            return vmxml.Element('memory',
                                 snapshot='external',
                                 file=memory_volume_path)

        def vm_conf_for_memory_snapshot():
            """Returns the needed vm configuration with the memory snapshot"""

            return {'restoreFromSnapshot': True,
                    '_srcDomXML': self._vm.migratable_domain_xml(),
                    'elapsedTimeOffset': time.time() - self._vm.start_time}

        snap = vmxml.Element('domainsnapshot')
        disks = vmxml.Element('disks')
        new_drives = {}
        vm_drives = {}

        for drive in self._snap_drives:
            base_drv, tget_drv = norm_snap_drive_params(drive)

            try:
                self._vm.findDriveByUUIDs(tget_drv)
            except LookupError:
                # The vm is not already using the requested volume for the
                # snapshot, continuing.
                pass
            else:
                # The snapshot volume is the current one, skipping
                self._vm.log.debug("The volume is already in use: %s",
                                   tget_drv)
                continue  # Next drive

            try:
                vm_drive = self._vm.findDriveByUUIDs(base_drv)
            except LookupError:
                # The volume we want to snapshot doesn't exist
                self._vm.log.error("The base volume doesn't exist: %s",
                                   base_drv)
                return response.error('snapshotErr')

            if vm_drive.hasVolumeLeases:
                self._vm.log.error('disk %s has volume leases', vm_drive.name)
                return response.error('noimpl')

            if vm_drive.transientDisk:
                self._vm.log.error('disk %s is a transient disk',
                                   vm_drive.name)
                return response.error('transientErr')

            vm_dev_name = vm_drive.name

            new_drives[vm_dev_name] = tget_drv.copy()
            new_drives[vm_dev_name]["type"] = "disk"
            new_drives[vm_dev_name]["diskType"] = vm_drive.diskType
            new_drives[vm_dev_name]["poolID"] = vm_drive.poolID
            new_drives[vm_dev_name]["name"] = vm_dev_name
            new_drives[vm_dev_name]["format"] = "cow"

            # We need to keep track of the drive object because
            # it keeps original data and used to generate snapshot element.
            # We keep the old volume ID so we can clear the block threshold.
            vm_drives[vm_dev_name] = (vm_drive, base_drv["volumeID"])

        prepared_drives = {}

        for vm_dev_name, vm_device in new_drives.items():
            # Adding the device before requesting to prepare it as we want
            # to be sure to teardown it down even when prepareVolumePath
            # failed for some unknown issue that left the volume active.
            prepared_drives[vm_dev_name] = vm_device
            try:
                new_drives[vm_dev_name]["path"] = \
                    self._vm.cif.prepareVolumePath(new_drives[vm_dev_name])
            except Exception:
                self._vm.log.exception('unable to prepare the volume path for '
                                       'disk %s', vm_dev_name)
                rollback_drives(prepared_drives)
                return response.error('snapshotErr')

            drive, _ = vm_drives[vm_dev_name]
            snapelem = drive.get_snapshot_xml(vm_device)
            disks.appendChild(snapelem)

        snap.appendChild(disks)

        snap_flags = (libvirt.VIR_DOMAIN_SNAPSHOT_CREATE_REUSE_EXT
                      | libvirt.VIR_DOMAIN_SNAPSHOT_CREATE_NO_METADATA)

        if self._memory_params:
            # Save the needed vm configuration
            # TODO: this, as other places that use pickle.dump
            # directly to files, should be done with outOfProcess
            vm_conf_vol = self._memory_params['dstparams']
            vm_conf_vol_path = self._vm.cif.prepareVolumePath(vm_conf_vol)
            try:
                with open(vm_conf_vol_path, "rb+") as f:
                    vm_conf = vm_conf_for_memory_snapshot()
                    # protocol=2 is needed for clusters < 4.4
                    # (for Python 2 host compatibility)
                    data = pickle.dumps(vm_conf, protocol=2)

                    # Ensure that the volume is aligned; qemu-img may segfault
                    # when converting unligned images.
                    # https://bugzilla.redhat.com/1649788
                    aligned_length = utils.round(len(data), 4096)
                    data = data.ljust(aligned_length, b"\0")

                    f.write(data)
                    f.flush()
                    os.fsync(f.fileno())
            finally:
                self._vm.cif.teardownVolumePath(vm_conf_vol)

            # Adding the memory volume to the snapshot xml
            memory_vol = self._memory_params['dst']
            memory_vol_path = self._vm.cif.prepareVolumePath(memory_vol)
            snap.appendChild(memory_snapshot(memory_vol_path))
        else:
            memory_vol = memory_vol_path = None
            snap_flags |= libvirt.VIR_DOMAIN_SNAPSHOT_CREATE_DISK_ONLY

        snapxml = xmlutils.tostring(snap)
        # TODO: this is debug information. For 3.6.x we still need to
        # see the XML even with 'info' as default level.
        self._vm.log.info("%s", snapxml)

        self._snapshot_job['memoryVolPath'] = memory_vol_path
        self._snapshot_job['memoryVol'] = memory_vol
        self._snapshot_job['newDrives'] = new_drives
        vm_drives_serialized = {}
        for k, v in vm_drives.items():
            vm_drives_serialized[k] = [xmlutils.tostring(v[0].getXML()), v[1]]
        self._snapshot_job['vmDrives'] = vm_drives_serialized
        write_snapshot_md(self._vm, self._snapshot_job, self._lock)

        # We need to stop the drive monitoring for two reasons, one is to
        # prevent spurious libvirt errors about missing drive paths (since
        # we're changing them), and also to prevent to trigger a drive
        # extension for the new volume with the apparent size of the old one
        # (the apparentsize is updated as last step in updateDriveParameters)
        self._vm.drive_monitor.disable()

        try:
            if self._should_freeze:
                self._vm.freeze()
            self._vm.log.info("Taking a live snapshot (drives=%s,"
                              "memory=%s)", ', '
                              .join(drive["name"] for drive in
                                    new_drives.values()),
                              self._memory_params is not None)
            try:
                self._vm.run_dom_snapshot(snapxml, snap_flags)
            except libvirt.libvirtError as e:
                if e.get_error_code() == libvirt.VIR_ERR_OPERATION_ABORTED:
                    with self._lock:
                        self._abort.set()
                        self._snapshot_job['abort'] = self._abort.is_set()
                    set_abort(self._vm, self._snapshot_job, self._completed,
                              self._abort, self._lock)
                    self._vm.log.info("Snapshot timeout reached,"
                                      " operation aborted")
                self._vm.log.exception("Unable to take snapshot")
                if self._abort.is_set():
                    # This will cause a jump into the finalize_vm.
                    # The abort is set and the finalize_vm will raise
                    # ActionStopped exception as well. This is an indicator
                    # to the Jobs framework signing a client abort of the job.
                    raise exception.ActionStopped()
                self._thaw_vm()
                raise exception.SnapshotFailed()
            set_completed(self._vm, self._snapshot_job, self._completed,
                          self._abort, self._lock)
            if self._completed.is_set():
                write_snapshot_md(self._vm, self._snapshot_job, self._lock)
                self._vm.log.info("Completed live snapshot")
        except:
            # In case the VM was shutdown in the middle of the snapshot
            # operation we keep doing the finalizing and reporting the failure.
            # Or, when the Job was aborted, finalize_vm will raise
            # ActionStopped exception to sign it was aborted by user(VDSM).
            self.finalize_vm(memory_vol)
            res = False
        else:
            res = self.teardown(memory_vol_path, memory_vol,
                                new_drives, vm_drives)
        if not res:
            raise RuntimeError("Failed to execute snapshot, "
                               "considering the operation as failure")


class LiveSnapshotRecovery(object):
    def __init__(self, vm, abort, completed, snapshot_job, lock):
        self._vm = vm
        self._job_stats = None
        self._snapshot_job = snapshot_job
        # The metadata stored in __init__ is in order to be able
        # to perform teardown.
        self._frozen = self._snapshot_job['frozen']
        self._memory_params = self._snapshot_job['memoryParams']
        self._job_uuid = self._snapshot_job['jobUUID']
        self._abort = abort
        self._completed = completed
        self._lock = lock

    @logutils.traceback()
    def run(self):
        self._vm.log.info("JOBMON: Checking job on VM: %s", self._vm.id)
        while True:
            try:
                # Only one job can run per VM. Therefore, given the fact we
                # have the metadata of the snapshot job and there is a job
                # still running (or the job is gone),
                # we can assume it was the snapshot job.
                self._job_stats = self._vm.job_stats()
            except libvirt.libvirtError:
                # TODO: This is workaround for
                #  https://bugzilla.redhat.com/1565552
                pass
            else:
                if self._job_stats and \
                        self._job_stats['type'] in (
                        libvirt.VIR_DOMAIN_JOB_NONE,
                        libvirt.VIR_DOMAIN_JOB_COMPLETED):
                    break
            self._vm.log.info("JOBMON: Snapshot is running")
            time.sleep(10)
        set_completed(self._vm, self._snapshot_job, self._completed,
                      self._abort, self._lock)
        if self._completed.is_set():
            write_snapshot_md(self._vm, self._snapshot_job, self._lock)
        self._vm.log.info("JOBMON: Snapshot isn't running")
        memory_vol_path = memory_vol = new_drives = vm_drives = None

        # In recovery the VM is in actual pause status.
        # We expect the VM will be reported as running by libvirt
        # shortly after the job is finished.
        # In that case, we will change the VM status to UP in VDSM.
        # We will check libvirt status report for 10 times,
        # every 1 seconds to eliminate possible race on the VM status.
        if self._vm.lastStatus == vmstatus.PAUSED:
            for _ in range(10):
                if self._vm.isDomainRunning():
                    self._vm.set_last_status(vmstatus.UP, vmstatus.PAUSED)
                    self._vm.send_status_event()
                    break
                time.sleep(1)
        try:
            # Taking the values from the VM metadata.
            # If we fail, then we are missing the data and will try
            # to recovery without them. It might be that we didn't start
            # the operation in libvirt and therefore we don't have them.
            # We still must perform teardown in such a case,
            # with incomplete metadata stored in __init__.
            memory_vol_path = self._snapshot_job['memoryVolPath']
            memory_vol = self._snapshot_job['memoryVol']
            new_drives = self._snapshot_job['newDrives']
            vm_drives = self._snapshot_job['vmDrives']
            for k, v in vm_drives.items():
                vm_drives[k] = (vmdevices.storage.Drive(
                    self._vm.log, **storagexml.parse(
                        xmlutils.fromstring(v[0]), {})
                ), v[1])
        except KeyError:
            self._vm.log.error("Missing data on the snapshot job "
                               "metadata.. calling teardown")
        snap = Snapshot(
            self._vm, None, self._memory_params, self._frozen, self._job_uuid,
            self._abort, self._completed, 0, 0, self._snapshot_job, self._lock
        )
        if self._abort.is_set():
            snap.finalize_vm(memory_vol)
            res = False
        else:
            res = snap.teardown(memory_vol_path, memory_vol,
                                new_drives, vm_drives)
        if not res:
            raise RuntimeError("Failed in snapshot recovery, "
                               "considering the operation as failure")


class AbortSnapshot(object):
    def __init__(self, vm, job_uuid, start_time, timeout,
                 abort, completed, snapshot_job, lock):
        self._vm = vm
        self._job_uuid = job_uuid
        self._start_time = start_time
        self._timeout = timeout
        self._abort = abort
        self._completed = completed
        self._snapshot_job = snapshot_job
        self._lock = lock
        self._thread = concurrent.thread(
            self.run, name="snap_abort/" + job_uuid[:8])

    def start(self):
        self._thread.start()

    def join(self):
        self._thread.join()

    @logutils.traceback()
    def run(self):
        monitoring_interval = min(60, self._timeout // 10)
        self._vm.log.info("Starting snapshot abort job, "
                          "with check interval %s",
                          monitoring_interval)
        # Waiting for the job to run on libvirt
        if self._job_running_init():
            while self._timeout_not_reached() and self._job_running() and not \
                    self._completed.is_set():
                self._vm.log.info("Time passed: %s, out of %s",
                                  self._running_time(), self._timeout)
                time.sleep(monitoring_interval)
            if not self._job_completed():
                self._abort_job()
        elif not self._job_completed():
            self._vm.log.error("Snapshot job didn't start on the domain")

    def _abort_job(self):
        if self._job_running():
            self._vm.log.warning("Timeout passed, aborting snapshot...")
            # We prefer to first set the abort because it's the less evil
            # scenario between having a snapshot, telling it was aborted and
            # failing the snapshot, telling it succeeded.
            # This is also better for optional racing with LiveSnapshotRecovery
            # thread.
            set_abort(self._vm, self._snapshot_job, self._completed,
                      self._abort, self._lock)
            try:
                self._vm.abort_domjob()
            except libvirt.libvirtError as e:
                self._vm.log.error("Failed to abort the job: %s", e)
        else:
            # We might get this debug error even when the job is completed,
            # when the VM memory is very small. This isn't harmful.
            self._vm.log.debug("The snapshot job isn't running "
                               "when trying to abort it")

    def _job_completed(self):
        if self._completed.is_set():
            self._vm.log.info("Snapshot job already completed")
            return True
        return False

    def _timeout_not_reached(self):
        return self._running_time() < self._timeout

    def _running_time(self):
        return monotonic_time() - self._start_time

    def _job_running(self):
        try:
            # Only one job can run per VM.
            job_stats = self._vm.job_stats()
        except libvirt.libvirtError:
            # TODO: This is workaround for
            #  https://bugzilla.redhat.com/1565552
            return False
        if job_stats and job_stats['type'] not in (
                libvirt.VIR_DOMAIN_JOB_NONE, libvirt.VIR_DOMAIN_JOB_COMPLETED
        ):
            return True
        return False

    def _job_running_init(self):
        while not self._job_running() and not self._completed.is_set() and \
                self._timeout_not_reached() and not self._abort.is_set():
            time.sleep(1)
        if self._job_running():
            self._vm.log.debug("The snapshot job is running")
            return True
        return False
