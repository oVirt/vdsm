<!--
SPDX-FileCopyrightText: Red Hat, Inc.
SPDX-License-Identifier: GPL-2.0-or-later
-->

# Incremental backup support

In version 4.3, oVirt introduced support for incremental backup.

The feature simplifies, speeds up, and improve robustness by backing
up only changed blocks, and avoiding temporary snapshots.
Integration with backup applications is improved by supporting
backup and restore of raw guest data regardless of the underlying disk format.

The following methods are added to facilitate the incremental backup support API:
- VM.redefine_checkpoints: Redefines VM checkpoints with specified list.
- VM.start_backup: Starts a backup for specified VM and selected disks of this VM.
- VM.stop_backup: Stops a backup with specified UUID.
- VM.backup_info: Returns VM backup info for specified UUID.
- VM.delete_checkpoints: Deletes specified VM checkpoints.

## Flow overview

Libvirt is able to facilitate incremental backups by tracking disk checkpoints,
which are points in time against which it is easy to compute which portion of
the disk has changed.

In order to provide a means to optimize restore (perform a reconstruction of
the state of the disk), two backup variants are supported:
1. Full backup: a backup created from the creation of the disk to a given
point in time.
1. Incremental backup: a backup created from just the dirty portion of the disk
between the first checkpoint and the second backup operation.

Flow example:
- Perform a full backup to save a complete copy of the data.
- Create a new checkpoint.
- Continue to manipulate data.
- Perform incremental backup to save the tracked data changes since the checkpoint.

### Backup

1. Redefine VM checkpoints (saved on engine side), this is required as all
checkpoint are deleted upon stopping the VM.

2. Invoke start backup: specify both 'from_checkpoint_id' and 'to_checkpoint_id'
for incremental backup, or omit 'from_checkpoint_id' for full backup.

3. Using imageio disk transfer, download the changed data (tracked in qemu
using a dirty bitmap).

4. Invoke stop backup to complete the operation.

### Restore

1. User selects restore point based on available backups using the backup
application (not part of oVirt).

2. Backup application creates a new disk or a snapshot with existing disk
to hold the restored data.

3. Backup application starts an upload image transfer for every disk,
specifying format=raw. This enable format conversion when uploading raw data
to qcow2 disk.

4. Backup application transfer the data included in this restore point to
imageio using HTTP API.

5. Backup application finalize the image transfers.

## Redefine Checkpoints

The following request redefines VM checkpoints with a specified list.
This is required as stopping a VM deletes all its associated checkpoints.

```json
{
  "vmId": "vm-id",
  "checkpoints": [
    {
      "id": "check-7",
      "created": 1545150905,
      "disks": [
        {"sd_id": "sd-id", "img_id": "img-id-1", "vol_id": "vol-id-1"},
        {"sd_id": "sd-id", "img_id": "img-id-2", "vol_id": "vol-id-2"}
      ]
    }
  ]
}
```

## Start Backup

The following request starts an incremental backup with a checkpoint.
Should be invoked after redefining the previous checkpoints.

```json
{
  "vmId": "vm-id",
  "backup_id": "backup-id",
  "disks": [
    {"sd_id": "sd-id", "img_id": "img-id-1", "vol_id": "vol-id-1"},
    {"sd_id": "sd-id", "img_id": "img-id-2", "vol_id": "vol-id-2"}
  ],
  "from_checkpoint_id": "check-9",
  "to_checkpont_id": "check-10"
}
```

Response:
```json
{
  "disks": {
    "img-id-1": "nbd:unix:/run/....sock:exportname=sda",
    "img-id-2": "nbd:unix:/run/....sock:exportname=sdb"
  }
}
```

Note: omitting "from_checkpoint_id" from the request will result
with a full backup of the disks.

## Stop Backup

The following request stops a backup.
Should be invoked to complete the backup flow.

```json
{
  "vmId": "vm-id",
  "backup_id": "backup-id"
}
```

## Backup Info

The following request retrieves information about a specific backup.
Can be used as an indication that a backup has been started.

```json
{
  "vmId": "vm-id",
  "backup_id": "backup-id"
}
```

Response:
```json
{
  "disks": {
    "img-id-1": "nbd:unix://run/....sock:exportname=sda",
    "img-id-2": "nbd:unix://run/....sock:exportname=sdb"
  }
}
```

## Delete Checkpoints

The following request deletes checkpoints by a specified list.
Can be used when cleanup is required.

```json
{
  "vmId": "vm-id",
  "checkpoints": [
    "check-5",
    "check-6"
  ]
}
```