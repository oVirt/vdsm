<!--
SPDX-FileCopyrightText: Red Hat, Inc.
SPDX-License-Identifier: GPL-2.0-or-later
-->

# Managed Volumes

In version 4.3, oVirt introduced support of Managed Block Storage.

Many storage vendors provide a offloading API allowing to do fast
storage side actions with minimal network usage from the virt management
side. Such APIs already are integrated in Cinder.

This feature enables the user to be able to consume any storage backend
supported in Cinder in order to create virtual disks for its VMs,
without the need of a full Openstack deployment by using CinderLib.

Engine communicates with the Storage Management API for provisioning
and exposing volumes.

Vdsm attaches/detaches the volumes using os_brick library according to
the information passed by Engine.

## Connector Information

In order to expose a volume to a specific host, Engine needs to
provide data from the specific host to the Storage Management API.

This data is provided as part of existing verb GetCapabilities as a new
entry called 'connector_info'.

The structure of the data is according to os_brick/Cinder format.

Note that currently os-brick package is not required in Vdsm spec as
it is not available in oVirt channels.  Therefore, the presence of the
package is tested before trying to invoke it. If os-brick is not
available, the new entry will not be available.

Here an example of the connector information:

```json
{
  "connector_info":
  {
    "initiator": "iqn.1994-05.com.redhat:3f2b2be7ebc",
    "ip": "1.2.3.4",
    "platform": "x86_64",
    "host": "vdsm-dev",
    "do_local_attach": "False",
    "os_type": "linux2",
    "multipath": "True"
  }
}
```

## Attach a volume

When the user wants to run a VM, Engine will first call CinderLib
API to expose the volume to the host according to the "connector_info".
The Storage Management API will provide the connection information
needed by the host to attach.  Then Engine calls the
ManagedVolume.attach_volume with this information and the volume ID.
The structure of the data is according to os_brick/Cinder format.

Here an example of the connection information (iSCSI volume):

```json
{
    "driver_volume_type": "iscsi",
    "data":
    {
        "target_lun": 26,
        "target_iqn": "iqn.2009-01.com.kaminario:storage.k2.22612",
        "target_portal": "3.2.1.1:3260",
        "target_discovered": "True"
    }
}
```

Vdsm will call an os_brick API to attach the volume and will return
the volume information data to Engine that will persist it in the DB.
The volume information consists of the volume path that Engine will
use in the VM XML and of the volume attachment as returned from os_brick.
The structure of the volume attachment data is according to os_brick/Cinder
format.

Here examples of the volume information:

For iSCSI volume with multipath:

```json
{
  "path" : "/dev/mapper/20024f400585401ce",
  "attachment":
  {
    "path": "/dev/dm-25",
    "scsi_wwn": "20024f400585401ce",
    "type": "block"
    "multipath_id": "20024f400585401ce"
  }
}
```

For RBD volume:

```json
{
  "path" : "/dev/rbd1",
  "attachment":
  {
    "path": "/dev/rbd1",
    "type": "block",
    "conf": "/tmp/brickrbd_WimcIm"
  }
}
```

Vdsm stores the volume information along with the connection
information of the attached volumes in a local DB.
The volume ID will be the key of the data in the DB.
The stored data is used to be able to filter them from GetDeviceList
result and to detach a volume.


## Detach a volume

When the user wants to stop a VM, Engine will call Vdsm to detach
the volume with the volume ID using ManagedVolume.detach_volume.

The needed data (device info and connection info) for performing the
detachment is retrieved from the local DB.


## Get volume information

The user is able to get the information of specific volumes using
ManagedVolume.volumes_info with the volume IDs. List of all volumes can be
obtained by using ManagedVolume.volumes_info without specifying a volume id.
Vdsm will return the volume information, similar to the attach flow.
Besides information contained in attach flow, it also includes volume ID and
parameter `exists`, which is set to `True` if multipath device is connected
and path exists on local machine.
For example:

```json
[{
  "vol_id": "01713ade-9688-43ff-a46c-0e2e35974dce",
  "exists": "True",
  "path" : "/dev/mapper/20024f400585401ce",
  "attachment":
  {
    "path": "/dev/sda",
    "scsi_wwn": "20024f400585401ce",
    "type": "block"
  }
}]
```
