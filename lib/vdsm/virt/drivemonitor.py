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
from __future__ import absolute_import

import libvirt

from vdsm.config import config
from vdsm.virt.vmdevices import storage


class ImprobableResizeRequestError(RuntimeError):
    pass


class DriveMonitor(object):
    """
    Track the highest allocation of thin-provisioned drives
    of a Vm, triggering the extension flow when needed.
    """

    def __init__(self, vm, log, enabled=True):
        self._vm = vm
        self._log = log
        self._enabled = enabled
        self._events_enabled = config.getboolean(
            'irs', 'enable_block_threshold_event')

    def enabled(self):
        return self._enabled

    def enable(self):
        self._enable = True
        self._log.info('Enabling drive monitoring')

    def disable(self):
        self._enable = False
        self._log.info('Disabling drive monitoring')

    def monitoring_needed(self):
        """
        Return True if a vm needs drive monitoring in this cycle.

        This is called every 2 seconds (configurable) by the periodic system.
        If this returns True, the periodic system will invoke
        monitor_drives during this periodic cycle.
        """
        return self._enabled and bool(self.monitored_drives())

    def set_threshold(self, drive, apparentsize):
        """
        Set the libvirt block threshold on the given drive, enabling
        libvirt to deliver the event when the threshold is crossed.
        Does nothing if the `_events_enabled` attribute is Falsey.

        Call this method when you need to set one initial block threshold
        (e.g. first time Vdsm monitors one drive), or after one volume
        extension, or when the top layer changes (after snapshot, after
        live storage migration completes).

        Args:
            drive: A storage.Drive object
            apparentsize: The drive apparent size in bytes (int)
        """
        if not self._events_enabled:
            return
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

        threshold = apparentsize - drive.watermarkLimit

        self._log.info(
            'setting block threshold to %d bytes for drive %r '
            '(apparentsize %d)',
            threshold, drive.name, apparentsize
        )
        try:
            # TODO: find a good way to expose Vm._dom as public property.
            # we are running out of names in Vm class.
            self._vm._dom.setBlockThreshold(drive.name, threshold)
        except libvirt.libvirtError as exc:
            # The drive threshold_state can be UNSET or EXCEEDED, and
            # this ensures that we will attempt to set the threshold later.
            drive.threshold_state = storage.BLOCK_THRESHOLD.UNSET
            self._log.error(
                'Failed to set block threshold on %r (%s): %s',
                drive.name, drive.path, exc)
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
        if index is None:
            target = drive.name
        else:
            target = '%s[%d]' % (drive.name, index)
        self._log.info('clearing block threshold for drive %r', target)
        # undocumented at libvirt level, need to deep dive to QEMU level
        # to learn this: set threshold to 0 disable the notification
        # another alternative could be just clear_threshold the events
        # we receive with monitoring disabled (flag at either Vm/drive
        # level). We will have races anyway.
        # TODO: file a libvirt documentation bug
        self._vm._dom.setBlockThreshold(target, 0)

    def on_block_threshold(self, dev, path, threshold, excess):
        """
        Callback to be executed in the libvirt event handler when
        a BLOCK_THRESHOLD event is delivered.

        Args:
            dev: device name (e.g. vda, sdb)
            path: device path
            threshold: the threshold (in bytes) that was exceeded
                       causing the event to trigger
            excess: amount (in bytes) written past the threshold
        """
        self._log.info('block threshold %d exceeded on %r (%s)',
                       threshold, dev, path)
        try:
            drive = self._vm.findDriveByName(dev)
        except LookupError:
            self._log.warning(
                'Unknown drive %r for vm %s - ignored block threshold event',
                dev, self._vm.id)
        else:
            drive.on_block_threshold(path)

    def monitored_drives(self):
        """
        Return the drives that need to be checked for extension
        on the next monitoring cycle.

        If events are disabled, the reported drives are all the
        writable chunked drives plus all the drives being replicated
        to a chunked drive.

        If events are enabled, the reported drives are the subset
        of the above. We can can have two states:

        - threshold_state == UNSET
          Possible use cases are the first time we monitor a drive, or
          after set_threshold failure, or when a drive path has changed.
          We should set the threshold on these drives.

        - threshold_state == EXCEEDED
          We got a libvirt BLOCK_THRESHOLD event for this drive, and
          they should be extended.

        We use the libvirt BLOCK_THRESHOLD event to detect if a drive
        needs extension for writeable chunked drives, or non-chunked
        drives being replicated to a chunked drive.

        drive    format  replica  format  events  comments
        --------------------------------------------------
        block    cow     block    cow     yes
        block    cow     file     cow     yes
        file     cow     block    cow     yes
        network  cow     block    cow     yes   libgfapi

        These replication types are not supported:
        - network raw to any (ceph)
        - any to network (libvirt/qemu limit)

        Returns:
            iterable of storage.Drives that needs to be checked
            for extension.
        """
        drives = [drive for drive in self._vm.getDiskDevices()
                  if (drive.chunked or drive.replicaChunked) and not
                  drive.readonly]
        if not self._events_enabled:
            # we need to check everything every poll cycle.
            return drives
        return [drive for drive in drives
                if drive.threshold_state == storage.BLOCK_THRESHOLD.UNSET or
                drive.threshold_state == storage.BLOCK_THRESHOLD.EXCEEDED]
