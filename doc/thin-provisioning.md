<!--
SPDX-FileCopyrightText: Red Hat, Inc.
SPDX-License-Identifier: GPL-2.0-or-later
-->

# thin-provisioning support in Vdsm

Vdsm supports thin-provisioned drives backed by either block or
file-based storage.  This document explains how this feature is
implemented with block based storage.


## Implementation in Vdsm up until 4.20.3 (oVirt < 4.2)

### Monitoring drive watermark

Vdsm monitors thin provisioned drives or drives being replicated to thin
provisioned drives periodically.  During startup, DriveWatermarkMonitor
is created and scheduled with the periodic executor to run
VM.volume_monitor.monitor_volumes every 2 seconds (configurable) on all
VMs.

For each VM, we fetch the drives that should be monitored. We have 2
cases:

- Drives which are block based and use qcow2 format - called "chunked"
  drives.
- Drives which are not chunked, but are replicated to a chunked drive
  during live storage migration.

For each drive, we check the current allocation using
`virDomainGetBlockInfo` and in case of non-chunked drives, also
`Volume.getSize` Vdsm API.

Based on the allocation and the current configuration, we may decide to
extend the drive and/or the replica.


### Handling ENOSPC errors

The monitoring and extending mechanism cannot guarantee that we extend a
drive in time. If a VM is writing to a drive too fast, monitoring code
was delayed by a blocking call, or extending a drive was delayed or
failed, a VM may try to write behind the current disk size. In this case
qemu will pause the VM and we get a libvirt
VIR_DOMAIN_EVENT_ID_IO_ERROR_REASON event with ```ENOSPC``` reason.

When receiving such event, we call VM.volume_monitor.on_enospc()
on the paused VM.  We are likely to find that one or more drives are
too full, and trigger an extend of the drives.


### Extend drive flow

Extending a drive is an asynchronous operation. On the host running the
VM, vdsm send an extend message to the SPM host, using the storage
mailbox. The SPM extend the drive and send a response via the storage
mailbox. Back on the host running the VM, a thread in the storage
mailbox threadpool received the extend reply and call a callback to
complete the extend operation.

Extending a drive is extending the logical volume on the shared storage.
To consume the bigger drive, Vdsm refresh the logical volume on the host
running the VM. Once the logical volume is refreshed, qemu can access
the bigger drive.

Finally, the extend completion callback resume the VM, in case the VM
has paused during the extend operation.


### Extending drives during live storage migration

We have several cases depending on the type of drive and the type of the
drive we are replicating to:

- chunked to chunked - extend both drive and replica
- chunked to non-chunked - extend only the drive
- non-chunked to chunked - extend only the replica drive

Qemu error handling for replica drive is not as good as for drives. A VM
is not paused if writing to a replica drive fails. We try to avoid this
case by doubling the chunk size during live storage migration, and by
extending the replica drive first, before extending the drive.

In this flow, we do:

- extend the replica drive if needed
- extend the drive if needed

The delay for extending the drive is increased because of this, but if
the VM is paused, we can safely extend the drive and resume.


### Handling extend errors

Checking if a drive needs extension or trying to extend it may fail
because of many reasons:
- Libvirt call may time out or fail
- Vdsm storage apis may time out of fail
- Sending message to the SPM may fail
- Receiving reply from the SPM may fail
- Storage may not be available when trying to refresh a logical volume

Because the system is based on periodic monitoring, the operation will
be retried on the next volume monitoring internal.


### Pre-extension and post-reduce

In live merge flow we pre-extend drives before the operation, and reduce
the drives after the operation. This is less efficient then extending,
but like this approach as it unifies handling of live and cold merge.


### Configuration

These configuration options control thin provisioning:

- ```vars:vm_watermark_interval - How often should we check drive
  watermark on block storage for automatic extension of thin provisioned
  volumes (default 2 seconds).

- ```irs:volume_utilization_percent``` - Together with
 volume_utilization_chunk_mb, set the minimal free space before a thin
 provisioned block volume is extended. Use lower values to extend
 earlier (default 50%).

- ```irs:volume_utilization_chunk_mb``` - Size of extension chunk in
  megabytes, and together with volume_utilization_percent, set the free
  space limit. Use higher values to extend in bigger chunks (default
  1024 MiB).

- ```irs:enable_block_threshold_event``` - Use events, instead of
  polling, to check the write threshold on thin-provisioned block-based
  drives.


## Implementation in Vdsm 4.20.3 and onwards (oVirt >= 4.2)

We want to optimize volume monitoring using the "BLOCK_THRESHOLD" event
provided by libvirt >= 3.2. Instead of checking periodically if a drive
should be extended, we will mark a drive for extension when receiving a
libvirt block threshold event.

Libvirt cannot yet deliver events for all the flows and the storage
configurations oVirt supports. Please check the documentation of
the VolumeMonitor.monitored_volumes method in the thinp.py module
to learn when Vdsm can use events, and when Vdsm must keep polling the
drives.


### Configuration

For 4.2 we will keep a new configuration option to enable libvirt block
threshold events: ```irs:enable_block_threshold_event```.
If disabled, the system will keep the old behavior.


### Setting block threshold event

During startup or recovery, find all chunked drives, check their current
capacity, and set a libvirt block threshold event based on the
configuration.

The threshold is the same value used in the current code to extend a
drive. For example, with default configuration, if the drive allocation
is 3g, the threshold will be 2.5g.

If setting block threshold for a drive failed, the system should retry
the operation on the next volume monitor cycle.


### Handling block threshold events

When we receive a block threshold event, we mark the drive for
extension. When the periodic worker wakes up, it get a list of all the
drives marked for extension and try to extend them.

Some drives may be disabled for monitoring temporarily, for example
during live storage migration or during live merge pivot. These drives
will be skipped even if they are marked for extension.

When extension succeeds, it clears the "needs extension" flag, and
register a new block threshold for this drive.


### Monitoring drives during live storage migration

Libvirt does not report events for the replica drive.
To overcome this we monitor the replica drive via the source
drive, even if the source drive is non-chunked.


### Stopping monitoring temporarily

In some cases (e.g. live merge pivot), we need to temporarily stop
monitoring the drives. We start monitoring back the drives as soon as
possible.

If we receive a block threshold event while volume monitoring is disabled
for monitoring, we mark the drive for extension as usual, but the
extension request will not be handled until monitoring is enabled for
this drive.


### Running or recovering a VM

- Set the block thresholds when starting or recovering the VM
- When we get the threshold event, we mark the related drive for
  extension
- Up to 2 seconds (configurable) later, the periodic volume monitoring
  will trigger the extension flow
- When the extension flow ends, set a new block threshold in libvirt
  for the extended drive


### Snapshot

- When a snapshot operation was completed, switching a drive to a new
  path, clear the block threshold on the drive (done implicitely by
  the Drive object when its path changes).
- The periodic monitoring will pick up the drive on the next cycle,
  do all the checks and set a new threshold if needed.
- If a block threshold event is received for the old drive path, ignore
  the event.


### Extending a drive automatically

- When extension request finish, set a new block threshold
- If setting new block threshold fails, mark the drive so the next drive
  monitor cycle will retry the operation.


### Extending drive virtual size (no changes required)

- The user edits the properties of a disk from the oVirt Engine UI.
- Vdsm performs the extension.
- The guest sees the new size.
- We changed the drive virtual size: no change is needed with respect to
  the block threshold, which is about the drive physical size.


### Live Storage Migration (LSM)

- Keep the current block threshold events, so we don't miss an event
  during LSM.
- Keep the current flow with no changes.
- Events received during LSM will mark a drive for extension, but the
  volume monitor ignores this state during LSM, since it must check the
  drive and/or the replica explicitly.
- When LSM is completed:
  - If the new drive is chunked and the source drive was marked for
    extension, the destination must be extended, since it is an
    exact mirror of the source drive at the time we did the pivot,
    and it may contain new data after pivot.
    The drive will be picked for extension on the next monitoring cycle.
  - The old drive is not part of the libvirt chain after we pivot to
    the new drive. So it is not necessary to clear the block threshold
    event for the old drive
  - If the new drive is not chunked, and the drive was marked for
    extension, clear the threshold, as it is not relevant any more.
- If LSM failed, and a drive was marked for extension during LSM, it
  will extended on the next volume monitor cycle.


### Live Merge

- Disable monitoring on drive before pivot
- Keep the block threshold event as is, so we don't miss an event during
  pivot.
- If we receive a block threshold event during the pivot, the drive will
  be marked for extension, but the volume monitoring code will ingore
  this because the drive is diabled for monitoring.
- Perform a pivot
- If pivot succeeded:
  - The old drive is not part of the libvirt chain after we pivot to
    the new drive. So it is not necessary to clear the block threshold
    event for the old drive.
  - If the base volume had zero free space (unlikely but possible),
    and enough new data -depends on IRS configuration, 512 MiB by default-
    was written during the merge, base needs now extension.
    Once we pivot, we may need to be in EXCEEDED mode.
  - We cannot just copy EXCEEDED blindly from the old top layer to the
    bottom layer, since base is likely to have some free space before
    we start the merge. We will end up with a drive in EXCEEDED state
    that fails the extend check, and we will continue to check this
    drive periodically for no reason.
  - So we just consider the drive like a new one. We set the threshold
    state to UNSET. If the drive is not chunked (maybe this was merge
    into raw base volume), it's done. If the drive is chunked,
    it will be picked by the monitor on the next cycle.
  - Enable monitoring for the drive
- If pivot failed, enable monitoring for the drive. If the drive was
  marked for extension during the piovt, it will be extended on the next
  volume monitoring cycle.


### Live migration (no changes required)

- Keep the current flow with no changes.
- Upon extension, refresh the volume first on the destination host and
  after that on the source host to make sure VM won't use the new size
  before it's visible on the destination.
- If refresh fails on the destination, fail the extension also on the 
  source hosts, otherwise the disk will be corrupted once VM is started
  on the destination. Extension will be retried automatically.
- In case disk extension finishes after migration is finished and VM
  doesn't exists on the source host any more, disk extension finishes
  with error. However, the error is harmless as VM is already
  terminated on the source host.


### Benefits of this approach:

- Minimize libvirt calls during normal operation - we expect 90%
  reduction in number of calls.
- Much less work for the periodic workers, checking only drives during
  LSM, and extending drives marked for extension.
- Eliminates the major source of discarded worker
- Since volume monitor does nothing most of the time, delays in drive
  monitoring are unlikely, avoiding delays in extending drives, that may
  lead to pausing a VM.


### Drawbacks of this approach:

Once we detect one disk needs extension, we can wait up to 2 seconds
before to trigger extension.


### Future work

Avoid the delay between block threshold event is received until the
drive is extended by waking up the volume monitor when event is received.
