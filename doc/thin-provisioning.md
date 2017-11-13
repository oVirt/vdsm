# thin-provisioning support in Vdsm

Vdsm supports thin-provisioned drives backed by either block or
file-based storage.  This document explains how this feature is
implemented with block based storage.


## Implementation in Vdsm up until 4.20.3 (oVirt < 4.2)

### Monitoring drive watermark

Vdsm monitors thin provisioned drives or drives being replicated to thin
provisioned drives periodically.  During startup, DriveWatermarkMonitor
is created and scheduled with the periodic executor to run
VM.monitor_drives every 2 seconds (configurable) on all VMs.

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

When receiving such event, we call VM.monitor_drives() on the paused VM.
We are likely to find that one or more drives are too full, and trigger
an extend of the drives.


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
be retried on the next drive monitoring internal.


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

We want to optimize drive monitoring using the "BLOCK_THRESHOLD" event
provided by libvirt >= 3.2. Instead of checking periodically if a drive
should be extended, we will mark a drive for extension when receiving a
libvirt block threshold event.

Libvirt cannot yet deliver events for all the flows and the storage
configurations oVirt supports. Please check the documentation of
the DriveMonitor.monitored_drives method in the drivemonitor.py module
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
the operation on the next drive monitor cycle.


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

Drives which are being replicated will use explicit polling since
libvirt does not report events for the replica drive yet. This issue is
tracked in http://bugzilla.redhat.com/XXX.


### Stopping monitoring temporarily

In some cases (e.g. live merge pivot), we need to temporarily stop
monitoring the drives. We start monitoring back the drives as soon as
possible.

If we receive a block threshold event while drive monitoring is disabled
for monitoring, we mark the drive for extension as usual, but the
extension request will not be handled until monitoring is enabled for
this drive.


### Running or recovering a VM

- Set the block thresholds when starting or recovering the VM
- When we get the threshold event, we mark the related drive for
  extension
- Up to 2 seconds (configurable) later, the periodic drive monitoring
  will trigger the extension flow
- When the extension flow ends, set a new block threshold in libvirt
  for the extended drive


### Snapshot

- When a snapshot operation was completed, switching a drive to a new
  path, set a new block threshold on the drive.
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
  drive montior ignored this state during LSM, since it must check the
  drive and/or the replica explicitily.
- When LSM is completed:
  - Clear the needs extension on the drive
  - Unregister the block threshold event for the old drive
  - If the new drive is chunked, register a new threshold event
- If LSM failed, and a drive was marked for extension during LSM, it
  will extended on the next drive monitor cycle.


### Live Merge

- Disable monitoring on drive before pivot
- Keep the block threshold event as is, so we don't miss an event during
  pivot.
- If we receive a block threshold event during the pivot, the drive will
  be marked for extension, but the drive monitoring code will ingore
  this because the drive is diabled for monitoring.
- Perform a pivot
- If pivot succeeded:
  - Clear the drive needs extension flag, if it was set during the
    pivot, since the event was received for a different path.
  - Unregister the block threshold for the old drive path
  - Register a new block threshold event for the new drive path
  - Enable monitoring for the drive
- If pivot failed, enable monitoring for the drive. If the drive was
  marked for extension during the piovt, it will be extended on the next
  drive monitoring cycle.


### Live migration (no changes required)

- Disable monitoring for all the drives of the VM
- Start the migration
- If migrtion failed, enable monitoring for all the drives of the VM. If
  a drive was marked for extension during failed live storage migration,
  it will be extended on the next drive monitor cycle.


### Benefits of this approach:

- Minimize libvirt calls during normal operation - we expect 90%
  reduction in number of calls.
- Much less work for the periodic workers, checking only drives during
  LSM, and extending drives marked for extension.
- Eliminates the major source of discarded worker
- Since drive monitor does nothing most of the time, delays in drive
  monitoring are unlikely, avoiding delays in extending drives, that may
  lead to pausing a VM.


### Drawbacks of this approach:

Once we detect one disk needs extension, we can wait up to 2 seconds
before to trigger extension.


### Future work

Avoid the delay between block threshold event is received until the
drive is extended by waking up the drive monitor when event is received.
