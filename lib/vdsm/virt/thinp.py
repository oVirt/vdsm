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

import re
import sys
import time

from collections import namedtuple

import libvirt

from vdsm.common import exception
from vdsm.common.config import config
from vdsm.common.time import Clock
from vdsm.virt import virdomain
from vdsm.virt.vmdevices import lookup
from vdsm.virt.vmdevices import storage

# Block device Information from libvirt block stats API.
BlockInfo = namedtuple("BlockInfo", [
    "index",
    "name",
    "path",
    "allocation",
    "capacity",
    "physical",
    "threshold",
])


class ImprobableAllocationError(RuntimeError):
    """
    Raised if libvirt reports improbable allocation value.
    """


class VolumeMonitor(object):
    """
    Track the highest allocation of thin-provisioned volumes of a Vm,
    triggering the extension flow when needed.
    """

    def __init__(self, vm, log, enabled=True):
        self._vm = vm
        self._log = log
        self._enabled = enabled

    # Enabling and disabling the monitor.

    def enabled(self):
        return self._enabled

    def enable(self):
        """
        Enable the volume monitor, does not raise.
        """
        self._enabled = True
        self._log.info('Enabling volume monitoring')

    def disable(self):
        """
        Disable the volume monitor, does not raise.
        """
        self._enabled = False
        self._log.info('Disabling volume monitoring')

    def monitoring_needed(self):
        """
        Return True if a vm needs volume monitoring in this cycle.

        This is called every 2 seconds (configurable) by the periodic system.
        If this returns True, the periodic system will invoke
        monitor_volumes during this periodic cycle.
        """
        return self._enabled and bool(self._monitored_volumes())

    # Managing libvirt block threshold.

    def _set_threshold(self, drive, apparentsize, index):
        """
        Set the libvirt block threshold on the given drive image, enabling
        libvirt to deliver the event when the threshold is crossed.

        Call this method when you need to set one initial block threshold
        (e.g. first time Vdsm monitors one drive), or after one volume
        extension, or when the top layer changes (after snapshot, after
        live storage migration completes).

        If index is None, register the top layer. Otherwise register the
        image with the given index. The index can be extracted from
        libvirt xml.

        Args:
            drive (storage.Drive): drive to register threshold event
            apparentsize (int): The drive apparent size in bytes
            index (int): Libvirt index of the layer in the chain
        """
        # watermarkLimit tells us the minimum amount of free space a thin
        # provisioned must have to avoid the extension.
        # If the free space falls below this limit, we should extend.
        # thus the following holds:
        # Extend if
        #    physical - allocation < limit
        # or if (equivalent to the above)
        #    allocation > physical - limit
        # the libvirt event fires if allocation >= threshold,
        # so we just compute
        #    threshold = physical - limit

        # 1  is the minimum meaningful threshold.
        # 0  is valid, but should be used only in clear_threshold
        # <0 means that apparentsize is too low, likely storage issue
        # that should be already handled -or at least notified- elsewhere.
        threshold = max(1, apparentsize - drive.watermarkLimit)

        target = format_target(drive.name, index)
        self._log.info(
            'Setting block threshold to %s bytes for drive %r apparentsize %s',
            threshold, target, apparentsize
        )
        try:
            # TODO: find a good way to expose Vm._dom as public property.
            # we are running out of names in Vm class.
            self._vm._dom.setBlockThreshold(target, threshold)
        except libvirt.libvirtError as exc:
            # The drive threshold_state can be UNSET or EXCEEDED, and
            # this ensures that we will attempt to set the threshold later.
            drive.threshold_state = storage.BLOCK_THRESHOLD.UNSET

            # If VM is stopped before disk extension finishes (e.g. during
            # migration), no need to log an error as libvirt call is expected
            # to fail.
            if exc.get_error_code() == libvirt.VIR_ERR_OPERATION_INVALID:
                self._log.debug(
                    "Domain not connected, skipping set block threshold for"
                    "drive %r: %s", drive.name, exc)
            else:
                self._log.error(
                    'Failed to set block threshold for drive %r (%s): %s',
                    drive.name, drive.path, exc)
        except Exception:
            drive.threshold_state = storage.BLOCK_THRESHOLD.UNSET
            raise
        else:
            drive.threshold_state = storage.BLOCK_THRESHOLD.SET

    def clear_threshold(self, drive, index):
        """
        Clear the libvirt block threshold on the given drive, disabling
        libvirt events.

        Args:
            drive: A storage.Drive object
            index: Optional index (int) of the element of the backing chain
                   to clear. If None (default), use the top layer.
        """
        target = format_target(drive.name, index)
        self._log.info('Clearing block threshold for drive %r', target)

        # undocumented at libvirt level, need to deep dive to QEMU level
        # to learn this: set threshold to 0 disable the notification
        # another alternative could be just clear_threshold the events
        # we receive with monitoring disabled (flag at either Vm/drive
        # level). We will have races anyway.
        # TODO: file a libvirt documentation bug
        self._vm._dom.setBlockThreshold(target, 0)

    def on_block_threshold(self, target, path, threshold, excess):
        """
        Callback to be executed in the libvirt event handler when
        a BLOCK_THRESHOLD event is delivered.

        Args:
            target: device name (vda) or indexed name (vda[7])
            path: device path
            threshold: the threshold (in bytes) that was exceeded
                       causing the event to trigger
            excess: amount (in bytes) written past the threshold
        """
        self._log.info('Block threshold %s exceeded by %s for drive %r (%s)',
                       threshold, excess, target, path)

        drive_name, index = parse_target(target)

        # We register only indexed name (vda[7]), but libvirt reports
        # also an event for the top volume (vda).
        # See https://bugzilla.redhat.com/1983429
        # TODO: Remove when bug is fixed.
        if index is None:
            self._log.debug(
                'Ignoring unexpected event for drive %r', drive_name)
            return

        try:
            drive = lookup.drive_by_name(
                self._vm.getDiskDevices()[:], drive_name)
        except LookupError:
            self._log.warning(
                'Unknown drive %r for vm %s - ignored block threshold event',
                drive_name, self._vm.id)
        else:
            drive.on_block_threshold(path)

    def on_enospc(self, drive):
        """
        Called when a VM pauses because of ENOSPC error writing to drive.
        """
        drive.on_enospc()

    # Monitoring volumes.

    def monitor_volumes(self):
        """
        Check and extend drives if needed.
        """
        drives = self._monitored_volumes()
        if not drives:
            return

        if not self._update_block_info(drives):
            return

        timeout = config.getfloat("thinp", "monitor_timeout")
        for drive in drives:
            try:
                with drive.monitor_lock(timeout):
                    self._extend_drive_if_needed(drive)
            except storage.MonitorBusy:
                self._log.debug(
                    "Timeout acquiring monitor lock for drive %s, retrying "
                    "in next monitoring cycle",
                    drive.name)

    def _update_block_info(self, drives):
        """
        Query libvirt block stats and update drives block info. This must be
        done on every monitoring cycle, before we decide if a drive should be
        extended.

        Return True if the update was successful.
        """
        try:
            block_stats = self._query_block_stats()
        except libvirt.libvirtError as e:
            self._log.error("Unable to get block stats: %s", e)
            return False

        for drive in drives:
            self._query_block_info(drive, drive.volumeID, block_stats)

        return True

    def _extend_drive_if_needed(self, drive):
        """
        Check if a drive should be extended, and start extension flow if
        needed.
        """
        if drive.threshold_state == storage.BLOCK_THRESHOLD.UNSET:
            self._handle_unset(drive)
        elif drive.threshold_state == storage.BLOCK_THRESHOLD.SET:
            # Extend completed during monitoring, nothing to do.
            pass
        elif drive.threshold_state == storage.BLOCK_THRESHOLD.EXCEEDED:
            self._handle_exceeded(drive)
        else:
            self._log.warning(
                "Unexpected threshold state %s for drive %s",
                drive.threshold_state, drive.name)

    def _handle_unset(self, drive):
        """
        Handle drive with UNSET threshold state.
        """
        if self._drive_needs_extend(drive):
            # The threshold was already exceeded, so qemu will never trigger a
            # block threshold event. Synthesize a block threshold event, and
            # handle the drive as if we received an event right now.
            drive.on_block_threshold(drive.path)
            self._handle_exceeded(drive)
        else:
            # Set block threshold. on_block_threshold() will be called when the
            # guest exceeds the threshold. If we will miss the event,
            # on_enospc() will be called.
            self._set_threshold(
                drive, drive.block_info.physical, drive.block_info.index)

    def _handle_exceeded(self, drive):
        """
        Handle drive with EXCEEDED threshold state.
        """
        if not self._can_extend_drive(drive):
            # Can happen with the default chunk size and utilization if drive
            # size <= 22g, drive is extended to maximum size, and the guest
            # writes to the end of the device.
            #
            # Example with 22g volume:
            #   qcow2 overhead:         1%
            #   maximum allocation:     24.20g
            #   maximum write offset:   22.22g
            #   block threshold:        22.20g
            #
            # We disable monitoring for the drive. Monitoring will be enabled
            # again if the drive will be resized.
            self._log.info(
                "Drive %s extended to maximum size, disabling monitoring",
                drive.name)
            drive.threshold_state = storage.BLOCK_THRESHOLD.DISABLED
            return

        self._log.info(
            "Requesting an extension for volume %s on domain %s block_info %s",
            drive.volumeID, drive.domainID, drive.block_info)

        self.extend_volume(
            drive,
            drive.volumeID,
            drive.block_info.physical,
            drive.block_info.capacity)

    def _can_extend_drive(self, drive):
        # NOTE: Physical may be larger than maximum volume size since it is
        # rounded up to the next lvm extent.
        max_size = drive.getMaxVolumeSize(drive.block_info.capacity)
        return drive.block_info.physical < max_size

    def _drive_needs_extend(self, drive):
        nextPhysSize = drive.getNextVolumeSize(
            drive.block_info.physical, drive.block_info.capacity)

        # NOTE: the intent of this check is to prevent faulty images to
        # trick qemu in requesting extremely large extensions (BZ#998443).
        # Probably the definitive check would be comparing the allocated
        # space with capacity + format_overhead. Anyway given that:
        #
        # - format_overhead is tricky to be computed (it depends on few
        #   assumptions that may change in the future e.g. cluster size)
        # - currently we allow only to extend by one chunk at time
        #
        # the current check compares allocation with the next volume
        # size. It should be noted that allocation cannot be directly
        # compared with the volume physical size as it includes also the
        # clusters not written yet (pending).
        if drive.block_info.allocation > nextPhysSize:
            self._vm.pause(pauseCode='EOTHER')
            raise ImprobableAllocationError(
                "Improbable allocation, pausing the VM to avoid corruption: "
                f"{drive.block_info}")

        free_space = drive.block_info.physical - drive.block_info.allocation
        return free_space < drive.watermarkLimit

    def _monitored_volumes(self):
        """
        Return the drives that need to be checked for extension
        on the next monitoring cycle.

        Returns:
            iterable of storage.Drives that needs to be checked
            for extension.
        """
        return [drive for drive in self._vm.getDiskDevices()
                if drive.needs_monitoring()]

    # Querying libvirt

    def query_block_info(self, drive, vol_id):
        """
        Get block info for drive volume.

        The drive must be part of the backing chain. This does not work for
        volumes which are not in the backing chain like a scratch disks volume
        or the target volume in blockCopy.

        The updated block info is stored in the drive.
        """
        return self._query_block_info(drive, vol_id, self._query_block_stats())

    def _query_block_info(self, drive, vol_id, block_stats):
        index = self._vm.query_drive_volume_index(drive, vol_id)
        drive.block_info = self._amend_block_info(drive, block_stats[index])
        return drive.block_info

    def _query_block_stats(self):
        """
        Extract monitoring related info from libvirt block stats.

        Return mapping from volume backing index to its BlockInfo.
        """
        block_stats = self._vm.query_block_stats()
        result = {}

        for i in range(block_stats["block.count"]):
            # The index and name are required to identify the node using
            # indexed name ("vda[7]").

            index = block_stats.get(f"block.{i}.backingIndex")
            if index is None:
                continue  # cdrom

            name = block_stats.get(f"block.{i}.name")
            if name is None:
                self._log.warning(
                    "Missing block.%s.name in block stats, skipping node", i)
                continue

            result[index] = BlockInfo(
                index=index,
                name=name,
                path=block_stats.get(f"block.{i}.path"),  # for debugging.
                allocation=block_stats.get(f"block.{i}.allocation", 0),
                capacity=block_stats.get(f"block.{i}.capacity", 0),
                physical=block_stats.get(f"block.{i}.physical", 0),
                threshold=block_stats.get(f"block.{i}.threshold", 0),
            )

        return result

    def _amend_block_info(self, drive, block_info):
        """
        Amend block info from libvirt in case the drive is not chucked and is
        replicating to a chunked drive.
        """
        if not drive.chunked:
            # Libvirt reports watermarks only for the source drive, but for
            # file-based drives it reports the same alloc and physical, which
            # breaks our extend logic. Since drive is chunked, we must have a
            # disk-based replica, so we get the physical size from the replica.
            replica = drive.diskReplicate
            volsize = self._vm.getVolumeSize(
                replica["domainID"],
                replica["poolID"],
                replica["imageID"],
                replica["volumeID"])
            block_info = block_info._replace(physical=volsize.apparentsize)

        return block_info

    # Extending volumes.

    def extend_volume(self, vmDrive, volumeID, curSize, capacity,
                      callback=None):
        """
        Extend drive volume and its replica volume during replication.

        Must be called only when the drive or its replica are chunked.

        If callback is specified, it will be invoked when the extend operation
        completes. If extension fails, the callback is called with an exception
        object. The callback signature is:

            def callback(error=None):

        """
        newSize = vmDrive.getNextVolumeSize(curSize, capacity)

        # If drive is replicated to a block device, we extend first the
        # replica, and handle drive later in _extend_replica_completed.

        # Used to measure the total extend time for the drive and the replica.
        # Note that the volume is extended after the replica is extended, so
        # the total extend time includes the time to extend the replica.
        clock = Clock()

        # If we received a block threshold event for this drive, include
        # the time since we received the event in the total time.
        # Otherwise measure only the time to extend the volume.
        if vmDrive.exceeded_time:
            clock.start("total", start_time=vmDrive.exceeded_time)
            clock.start("wait", start_time=vmDrive.exceeded_time)
            clock.stop("wait")
        else:
            clock.start("total")

        if vmDrive.replicaChunked:
            self._extend_replica(
                vmDrive, newSize, clock, callback=callback)
        else:
            self._extend_volume(
                vmDrive, volumeID, newSize, clock, callback=callback)

    def _extend_replica(self, drive, newSize, clock, callback=None):
        clock.start("extend-replica")
        volInfo = {
            'domainID': drive.diskReplicate['domainID'],
            'imageID': drive.diskReplicate['imageID'],
            'name': drive.name,
            'newSize': newSize,
            'poolID': drive.diskReplicate['poolID'],
            'volumeID': drive.diskReplicate['volumeID'],
            'clock': clock,
            'callback': callback,
        }
        self._log.debug(
            "Requesting an extension for the volume replication: %s",
            volInfo)
        self._vm.cif.irs.sendExtendMsg(
            drive.poolID, volInfo, newSize, self._extend_replica_completed)

    def _extend_replica_completed(self, volInfo):
        clock = volInfo["clock"]
        clock.stop("extend-replica")

        with clock.run("refresh-replica"):
            self._vm.refresh_volume(volInfo)

        self._verify_volume_extension(volInfo)
        vmDrive = lookup.drive_by_name(
            self._vm.getDiskDevices()[:], volInfo['name'])
        if not vmDrive.chunked:
            # This was a replica only extension, we are done.
            clock.stop("total")
            self._log.info(
                "Extend replica %s completed %s", volInfo["volumeID"], clock)
            return

        self._log.debug(
            "Requesting extension for the original drive: %s (domainID: %s, "
            "volumeID: %s)",
            vmDrive.name, vmDrive.domainID, vmDrive.volumeID)
        self._extend_volume(
            vmDrive, vmDrive.volumeID, volInfo['newSize'], clock,
            callback=volInfo["callback"])

    def _extend_volume(self, vmDrive, volumeID, newSize, clock,
                       callback=None):
        clock.start("extend-volume")
        volInfo = {
            'domainID': vmDrive.domainID,
            'imageID': vmDrive.imageID,
            'internal': vmDrive.volumeID != volumeID,
            'name': vmDrive.name,
            'newSize': newSize,
            'poolID': vmDrive.poolID,
            'volumeID': volumeID,
            'clock': clock,
            'callback': callback,
        }
        self._log.debug("Requesting an extension for the volume: %s", volInfo)
        self._vm.cif.irs.sendExtendMsg(
            vmDrive.poolID, volInfo, newSize, self._extend_volume_completed)

    def _extend_volume_completed(self, volInfo):
        callback = None
        error = None
        try:
            callback = volInfo["callback"]
            clock = volInfo["clock"]
            clock.stop("extend-volume")

            if self._vm.should_refresh_destination_volume():
                with clock.run("refresh-destination-volume"):
                    self._vm.refresh_destination_volume(volInfo)

            # After a volume was refreshed, the monitor thread may wake up and
            # trigger unwanted extend to the next chunk size. Lock the drive
            # during refresh until we set a new threshold. If taking the lock
            # times out, we abort this attempt without refreshing the volume.
            # The next extend attempt will try to take the lock and refresh the
            # drive.

            drive = lookup.drive_by_name(
                self._vm.getDiskDevices()[:], volInfo['name'])
            timeout = config.getfloat("thinp", "refresh_timeout")

            with drive.monitor_lock(timeout):
                with clock.run("refresh-volume"):
                    self._vm.refresh_volume(volInfo)
                volSize = self._verify_volume_extension(volInfo)

                clock.stop("total")
                self._log.info(
                    "Extend volume %s completed %s",
                    volInfo["volumeID"], clock)

                if not volInfo['internal']:
                    self.update_drive_volume_size(drive, volSize)

            self._vm.extend_volume_completed()

        except exception.DiskRefreshNotSupported as e:
            self._log.warning(
                "Migration destination host does not support "
                "extending disk during migration, disabling disk "
                "extension during migration")
            self.disable()
            error = e
        except virdomain.NotConnectedError as e:
            self._log.debug("VM not running, aborting extend completion")
            error = e
        finally:
            if callback:
                callback(error=sys.exc_info()[1] or error)

    def _verify_volume_extension(self, volInfo):
        volSize = self._vm.getVolumeSize(
            volInfo['domainID'],
            volInfo['poolID'],
            volInfo['imageID'],
            volInfo['volumeID'])

        self._log.debug(
            "Verifying extension for volume %s, requested size %s, current "
            "size %s",
            volInfo['volumeID'], volInfo['newSize'], volSize.apparentsize)

        if volSize.apparentsize < volInfo['newSize']:
            raise RuntimeError(
                "Volume extension failed for %s (domainID: %s, volumeID: %s)" %
                (volInfo['name'], volInfo['domainID'], volInfo['volumeID']))

        return volSize

    def update_drive_volume_size(self, drive, volsize):
        """
        Updates drive's apparentsize and truesize, and set a new block
        threshold based on the new size.

        Arguments:
            drive (virt.vmdevices.storage.Drive): The drive object using the
                resized volume.
            volsize (virt.vm.VolumeSize): new volume size tuple
        """
        drive.apparentsize = volsize.apparentsize
        drive.truesize = volsize.truesize

        index = self._vm.query_drive_volume_index(drive, drive.volumeID)
        self._set_threshold(drive, volsize.apparentsize, index)


_TARGET_RE = re.compile(r"([hvs]d[a-z]+)\[(\d+)\]")


def format_target(name, index):
    return "{}[{:d}]".format(name, index)


def parse_target(target):
    match = _TARGET_RE.match(target)
    if match:
        name, index = match.groups()
        return name, int(index)
    else:
        return target, None
