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
from collections import namedtuple

import libvirt

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


class ImprobableResizeRequestError(RuntimeError):
    pass


class VolumeMonitor(object):
    """
    Track the highest allocation of thin-provisioned volumes of a Vm,
    triggering the extension flow when needed.
    """

    def __init__(self, vm, log, enabled=True):
        self._vm = vm
        self._log = log
        self._enabled = enabled

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
        return self._enabled and bool(self.monitored_volumes())

    def set_threshold(self, drive, apparentsize, index=None):
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

    def clear_threshold(self, drive, index=None):
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

    def monitor_volumes(self):
        """
        Return True if at least one volume is being extended, False otherwise.
        """
        drives = self.monitored_volumes()
        if not drives:
            return False

        try:
            block_stats = self.get_block_stats()
        except libvirt.libvirtError as e:
            self._log.error("Unable to get block stats: %s", e)
            return False

        extended = False
        for drive in drives:
            try:
                if self._extend_drive_if_needed(drive, block_stats):
                    extended = True
            except ImprobableResizeRequestError:
                break

        return extended

    def _extend_drive_if_needed(self, drive, block_stats):
        """
        Check if a drive should be extended, and start extension flow if
        needed.

        When libvirt BLOCK_THRESHOLD event handling is enabled (
        irs.enable_block_threshold_event == True), this method acts according
        the drive.threshold_state:

        - UNSET: the drive needs to register for a new block threshold,
                 so try to set it. We set the threshold both for chunked
                 drives and non-chunked drives replicating to chunked
                 drives.
        - EXCEEDED: the drive needs extension, try to extend it.
        - SET: this method should never receive a drive in this state,
               emit warning and exit.

        Return True if started an extension flow, False otherwise.
        """
        if drive.threshold_state == storage.BLOCK_THRESHOLD.SET:
            self._log.warning(
                "Unexpected state for drive %s: threshold_state SET",
                drive.name)
            return False

        index = self._vm.query_drive_volume_index(drive, drive.volumeID)
        block_info = self._vm.amend_block_info(drive, block_stats[index])
        drive.block_info = block_info

        if drive.threshold_state == storage.BLOCK_THRESHOLD.UNSET:
            self.set_threshold(drive, block_info.physical, index=index)

        if not self.should_extend_volume(drive, drive.volumeID, block_info):
            return False

        # TODO: if the threshold is wrongly set below the current allocation,
        # for example because of delays in handling the event, or if the VM
        # writes too fast, we will never receive an event.
        # We need to set the drive threshold to EXCEEDED both if we receive
        # one event or if we found that the threshold was exceeded during
        # the VolumeMonitor.should_extend_volume check.
        self.update_threshold_state_exceeded(drive)

        self._log.info(
            "Requesting extension for volume %s on domain %s block_info %s "
            "threshold_state %s",
            drive.volumeID, drive.domainID, block_info, drive.threshold_state)

        self._vm.extend_volume(
            drive, drive.volumeID, block_info.physical, block_info.capacity)

        return True

    def monitored_volumes(self):
        """
        Return the drives that need to be checked for extension
        on the next monitoring cycle.

        Returns:
            iterable of storage.Drives that needs to be checked
            for extension.
        """
        return [drive for drive in self._vm.getDiskDevices()
                if drive.needs_monitoring()]

    def should_extend_volume(self, drive, volumeID, block_info):
        nextPhysSize = drive.getNextVolumeSize(
            block_info.physical, block_info.capacity)

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
        if block_info.allocation > nextPhysSize:
            msg = ("Improbable extension request for volume %s on domain "
                   "%s, pausing the VM to avoid corruptions (block_info: %s"
                   ", next physical size: %s)" %
                   (volumeID, drive.domainID, block_info, nextPhysSize))
            self._log.error(msg)
            self._vm.pause(pauseCode='EOTHER')
            raise ImprobableResizeRequestError(msg)

        if block_info.physical >= drive.getMaxVolumeSize(block_info.capacity):
            # The volume was extended to the maximum size. physical may be
            # larger than maximum volume size since it is rounded up to the
            # next lvm extent.
            return False

        if (block_info.allocation == 0 and
                drive.threshold_state == storage.BLOCK_THRESHOLD.EXCEEDED):
            # We get allocation == 0:
            # - Before the guest write to the disk.
            # - Older libvirt versions did not report allocation during
            #   backup, see https://bugzilla.redhat.com/2015281.
            # If we got a threshold event, we can safely assume that the guest
            # wrote to the drive, and we need to extend it.
            self._log.warning(
                "No allocation info for drive %s, but block threshold was "
                "exceeded - assuming that drive needs extension",
                drive.name)
            return True

        free_space = block_info.physical - block_info.allocation
        return free_space < drive.watermarkLimit

    def update_threshold_state_exceeded(self, drive):
        if drive.threshold_state != storage.BLOCK_THRESHOLD.EXCEEDED:
            # if the threshold is wrongly set below the current allocation,
            # for example because of delays in handling the event,
            # or if the VM writes too fast, we will never receive an event.
            # We need to set the drive threshold to EXCEEDED both if we receive
            # one event or if we found that the threshold was exceeded during
            # the _shouldExtendVolume check.
            drive.threshold_state = storage.BLOCK_THRESHOLD.EXCEEDED
            self._log.info(
                "Drive %s needs to be extended, forced threshold_state "
                "to exceeded", drive.name)

    def get_block_stats(self):
        """
        Extract monitoring related info from libvirt block stats.

        Return mapping from volume backing index to its BlockInfo.
        """
        block_stats = self._vm.get_block_stats()
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


_TARGET_RE = re.compile(r"([hvs]d[a-z]+)\[(\d+)\]")


def format_target(name, index):
    if index is None:
        return name
    else:
        return "{}[{}]".format(name, index)


def parse_target(target):
    match = _TARGET_RE.match(target)
    if match:
        name, index = match.groups()
        return name, int(index)
    else:
        return target, None
