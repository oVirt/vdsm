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
import time

import libvirt

from vdsm.common import logutils
from vdsm.common import response
from vdsm import utils
from vdsm.config import config
from vdsm.common import properties, xmlutils
from vdsm.common.compat import pickle

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


class Job(vdsm.virt.jobs.Job):

    def __init__(self, vm, snap_drives, memory_params,
                 frozen, job_uuid, recovery=False):
        super(Job, self).__init__(job_uuid, 'snapshot_vm')
        self.vm = vm
        self.snap_drives = snap_drives
        self.memory_params = memory_params
        self.frozen = frozen
        self.job_uuid = job_uuid
        self.recovery = recovery

    def _run(self):
        if self.recovery:
            LiveSnapshotRecovery(self.vm).run()
        else:
            snap = Snapshot(self.vm, self.snap_drives, self.memory_params,
                            self.frozen, self.job_uuid)
            snap.snapshot()


class Snapshot(properties.Owner):
    job_uuid = properties.UUID(required=True)

    def __init__(self, vm, snap_drives, memory_params, frozen, job_uuid):
        self.vm = vm
        self.snap_drives = snap_drives
        self.memory_params = memory_params
        self.frozen = frozen
        self.job_uuid = job_uuid
        # When creating memory snapshot libvirt will pause the vm
        self.should_freeze = not (self.memory_params or self.frozen)
        self._snapshot_job = {"jobUUID": job_uuid,
                              "frozen": self.frozen,
                              "memoryParams": self.memory_params}
        if self.snap_drives is not None:
            # Regular flow, not recovery
            self.vm.update_snapshot_metadata(self._snapshot_job)

    def _finalize_vm(self, memory_vol):
        try:
            self.vm.drive_monitor.enable()
            if self.memory_params:
                self.vm.cif.teardownVolumePath(memory_vol)
            if config.getboolean('vars', 'time_sync_snapshot_enable'):
                self.vm.syncGuestTime()
        finally:
            # Cleaning snapshot job metadata
            self._snapshot_job = None
            self.vm.update_snapshot_metadata(self._snapshot_job)

    def teardown(self, memory_vol_path, memory_vol, new_drives, vm_drives):
        self.vm.log.info('Starting snapshot teardown')
        result = True

        def pad_memory_volume(memory_vol_path, sd_uuid):
            sd_type = sd.name2type(
                self.vm.cif.irs.getStorageDomainInfo(sd_uuid)['info']['type'])
            if sd_type in sd.FILE_DOMAIN_TYPES:
                iop = oop.getProcessPool(sd_uuid)
                iop.fileUtils.padToBlockSize(memory_vol_path)

        try:
            # Must always thaw, even if freeze failed; in case the guest
            # did freeze the filesystems, but failed to reply in time.
            # Libvirt is using same logic (see src/qemu/qemu_driver.c).
            if self.should_freeze:
                self.vm.thaw()

            # We are padding the memory volume with block size of zeroes
            # because qemu-img truncates files such that their size is
            # round down to the closest multiple of block size (bz 970559).
            # This code should be removed once qemu-img will handle files
            # with size that is not multiple of block size correctly.
            if self.memory_params:
                pad_memory_volume(memory_vol_path, memory_vol['domainID'])

            for drive in new_drives.values():
                # Update the drive information
                _, old_volume_id = vm_drives[drive["name"]]
                try:
                    self.vm.updateDriveParameters(drive)
                except Exception:
                    # Here it's too late to fail, the switch already happened
                    # and there's nothing we can do, we must to proceed anyway
                    # to report the live snapshot success.
                    self.vm.log.exception("Failed to update drive information"
                                          " for '%s'", drive)

                drive_obj = lookup.drive_by_name(
                    self.vm.getDiskDevices()[:], drive["name"])
                self.vm.clear_drive_threshold(drive_obj, old_volume_id)

                try:
                    self.vm.updateDriveVolume(drive_obj)
                except vdsm.virt.vm.StorageUnavailableError as e:
                    # Will be recovered on the next monitoring cycle
                    self.vm.log.error("Unable to update drive %r "
                                      "volume size: %s", drive["name"], e)
        except Exception as e:
            self.vm.log.error("Snapshot teardown error: %s, "
                              "trying to continue teardown", e)
            result = False
        finally:
            self._finalize_vm(memory_vol)
        return result

    def __repr__(self):
        return ("<%s vm=%s job=%s 0x%s>" %
                (self.__class__.__name__, self.vm.id, self.job_uuid, id(self)))

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
                    self.vm.cif.teardownVolumePath(drive)
                except Exception:
                    self.vm.log.exception("Unable to teardown drive: %s",
                                          vm_dev_name)

        def memory_snapshot(memory_volume_path):
            """Libvirt snapshot XML"""

            return vmxml.Element('memory',
                                 snapshot='external',
                                 file=memory_volume_path)

        def vm_conf_for_memory_snapshot():
            """Returns the needed vm configuration with the memory snapshot"""

            return {'restoreFromSnapshot': True,
                    '_srcDomXML': self.vm.migratable_domain_xml(),
                    'elapsedTimeOffset': time.time() - self.vm.start_time}

        snap = vmxml.Element('domainsnapshot')
        disks = vmxml.Element('disks')
        new_drives = {}
        vm_drives = {}

        for drive in self.snap_drives:
            base_drv, tget_drv = norm_snap_drive_params(drive)

            try:
                self.vm.findDriveByUUIDs(tget_drv)
            except LookupError:
                # The vm is not already using the requested volume for the
                # snapshot, continuing.
                pass
            else:
                # The snapshot volume is the current one, skipping
                self.vm.log.debug("The volume is already in use: %s", tget_drv)
                continue  # Next drive

            try:
                vm_drive = self.vm.findDriveByUUIDs(base_drv)
            except LookupError:
                # The volume we want to snapshot doesn't exist
                self.vm.log.error("The base volume doesn't exist: %s",
                                  base_drv)
                return response.error('snapshotErr')

            if vm_drive.hasVolumeLeases:
                self.vm.log.error('disk %s has volume leases', vm_drive.name)
                return response.error('noimpl')

            if vm_drive.transientDisk:
                self.vm.log.error('disk %s is a transient disk', vm_drive.name)
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
                    self.vm.cif.prepareVolumePath(new_drives[vm_dev_name])
            except Exception:
                self.vm.log.exception('unable to prepare the volume path for '
                                      'disk %s', vm_dev_name)
                rollback_drives(prepared_drives)
                return response.error('snapshotErr')

            drive, _ = vm_drives[vm_dev_name]
            snapelem = drive.get_snapshot_xml(vm_device)
            disks.appendChild(snapelem)

        snap.appendChild(disks)

        snap_flags = (libvirt.VIR_DOMAIN_SNAPSHOT_CREATE_REUSE_EXT
                      | libvirt.VIR_DOMAIN_SNAPSHOT_CREATE_NO_METADATA)

        if self.memory_params:
            # Save the needed vm configuration
            # TODO: this, as other places that use pickle.dump
            # directly to files, should be done with outOfProcess
            vm_conf_vol = self.memory_params['dstparams']
            vm_conf_vol_path = self.vm.cif.prepareVolumePath(vm_conf_vol)
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
                self.vm.cif.teardownVolumePath(vm_conf_vol)

            # Adding the memory volume to the snapshot xml
            memory_vol = self.memory_params['dst']
            memory_vol_path = self.vm.cif.prepareVolumePath(memory_vol)
            snap.appendChild(memory_snapshot(memory_vol_path))
        else:
            memory_vol = memory_vol_path = None
            snap_flags |= libvirt.VIR_DOMAIN_SNAPSHOT_CREATE_DISK_ONLY

        snapxml = xmlutils.tostring(snap)
        # TODO: this is debug information. For 3.6.x we still need to
        # see the XML even with 'info' as default level.
        self.vm.log.info("%s", snapxml)

        self._snapshot_job['memoryVolPath'] = memory_vol_path
        self._snapshot_job['memoryVol'] = memory_vol
        self._snapshot_job['newDrives'] = new_drives
        vm_drives_serialized = {}
        for k, v in vm_drives.items():
            vm_drives_serialized[k] = [xmlutils.tostring(v[0].getXML()), v[1]]
        self._snapshot_job['vmDrives'] = vm_drives_serialized
        self.vm.update_snapshot_metadata(self._snapshot_job)

        # We need to stop the drive monitoring for two reasons, one is to
        # prevent spurious libvirt errors about missing drive paths (since
        # we're changing them), and also to prevent to trigger a drive
        # extension for the new volume with the apparent size of the old one
        # (the apparentsize is updated as last step in updateDriveParameters)
        self.vm.drive_monitor.disable()

        try:
            if self.should_freeze:
                self.vm.freeze()
            try:
                self.vm.log.info("Taking a live snapshot (drives=%s,"
                                 "memory=%s)", ', '
                                 .join(drive["name"] for drive in
                                       new_drives.values()),
                                 self.memory_params is not None)
                self.vm.run_dom_snapshot(snapxml, snap_flags)
                self.vm.log.info("Completed live snapshot")
            except libvirt.libvirtError:
                self.vm.log.exception("Unable to take snapshot")
                if self.should_freeze:
                    self.vm.thaw()
                return response.error('snapshotErr')
        except:
            # In case the VM was shutdown in the middle of the snapshot
            # operation we keep doing the finalizing and reporting the failure.
            self._finalize_vm(memory_vol)
            res = False
        else:
            res = self.teardown(memory_vol_path, memory_vol,
                                new_drives, vm_drives)
        if not res:
            raise RuntimeError("Failed to execute snapshot, "
                               "considering the operation as failure")


class LiveSnapshotRecovery(object):
    def __init__(self, vm):
        self.vm = vm
        self.job_stats = None
        self._snapshot_job = self.vm.snapshot_metadata()
        # The metadata stored in __init__ is in order to be able
        # to perform teardown.
        self.frozen = self._snapshot_job['frozen']
        self.memory_params = self._snapshot_job['memoryParams']
        self.job_uuid = self._snapshot_job['jobUUID']

    @logutils.traceback()
    def run(self):
        self.vm.log.info("JOBMON: Checking job on VM: %s", self.vm.id)
        while True:
            try:
                # Only one job can run per VM. Therefore, given the fact we
                # have the metadata of the snapshot job and there is a job
                # still running (or the job is gone),
                # we can assume it was the snapshot job.
                self.job_stats = self.vm.job_stats()
            except libvirt.libvirtError:
                # TODO: This is workaround for
                #  https://bugzilla.redhat.com/1565552
                pass
            else:
                if self.job_stats and \
                        self.job_stats['type'] == libvirt.VIR_DOMAIN_JOB_NONE:
                    break
            self.vm.log.info("JOBMON: Snapshot is running")
            time.sleep(10)

        self.vm.log.info("JOBMON: Snapshot isn't running")
        memory_vol_path = memory_vol = new_drives = vm_drives = None

        # In recovery the VM is in actual pause status.
        # We expect the VM will be reported as running by libvirt
        # shortly after the job is finished.
        # In that case, we will change the VM status to UP in VDSM.
        # We will check libvirt status report for 10 times,
        # every 1 seconds to eliminate possible race on the VM status.
        if self.vm.lastStatus == vmstatus.PAUSED:
            for _ in range(10):
                if self.vm.isDomainRunning():
                    self.vm.set_last_status(vmstatus.UP, vmstatus.PAUSED)
                    self.vm.send_status_event()
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
                    self.vm.log, **storagexml.parse(
                        xmlutils.fromstring(v[0]), {})
                ), v[1])
        except KeyError:
            self.vm.log.error("Missing data on the snapshot job "
                              "metadata.. calling teardown")
        snap = Snapshot(
            self.vm, None, self.memory_params, self.frozen, self.job_uuid
        )
        res = snap.teardown(memory_vol_path, memory_vol,
                            new_drives, vm_drives)
        if not res:
            raise RuntimeError("Failed in snapshot recovery, "
                               "considering the operation as failure")
