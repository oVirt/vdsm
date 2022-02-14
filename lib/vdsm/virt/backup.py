#
# Copyright 2019 Red Hat, Inc.
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
import os

import libvirt

from vdsm.common import exception
from vdsm.common import nbdutils
from vdsm.common import properties
from vdsm.common import response
from vdsm.common import xmlutils
from vdsm.common.constants import P_BACKUP

from vdsm.virt import virdomain
from vdsm.virt import vmxml
from vdsm.virt.vmdevices import storage
from vdsm.virt.vmdevices.storage import DISK_TYPE

log = logging.getLogger("storage.backup")

MODE_FULL = "full"
MODE_INCREMENTAL = "incremental"


class BackupDisk:

    def __init__(self, drive, backup_mode, scratch_disk):
        self.drive = drive
        self.backup_mode = backup_mode
        self.scratch_disk = scratch_disk


@virdomain.expose(
    "backupBegin",
    "abortJob",
    "backupGetXMLDesc",
    "checkpointLookupByName",
    "listAllCheckpoints",
    "checkpointCreateXML",
    "blockInfo"
)
class DomainAdapter(object):
    """
    VM wrapper class that exposes only
    libvirt backup related operations.
    """
    def __init__(self, vm):
        self._vm = vm


class ScratchDiskConfig(properties.Owner):
    path = properties.String(required=True)
    type = properties.Enum(
        required=True,
        values=[DISK_TYPE.FILE, DISK_TYPE.BLOCK])
    sd_id = properties.UUID(required=False)
    img_id = properties.UUID(required=False)
    vol_id = properties.UUID(required=False)

    def __init__(self, **kw):
        self.path = kw.get("path")
        self.type = kw.get("type")
        self.sd_id = kw.get("sd_id")
        self.img_id = kw.get("img_id")
        self.vol_id = kw.get("vol_id")


class DiskConfig(properties.Owner):
    vol_id = properties.UUID(required=True)
    img_id = properties.UUID(required=True)
    dom_id = properties.UUID(required=True)
    checkpoint = properties.Boolean(required=True)
    backup_mode = properties.Enum(values=("full", "incremental"))

    def __init__(self, disk_config):
        self.vol_id = disk_config.get("volumeID")
        self.img_id = disk_config.get("imageID")
        self.dom_id = disk_config.get("domainID")
        # Mark if the disk is included in the checkpoint.
        self.checkpoint = disk_config.get("checkpoint")
        self.backup_mode = disk_config.get("backup_mode")
        # Initialized when the engine creates the scratch
        # disk on a shared storage
        if "scratch_disk" in disk_config:
            scratch_disk = disk_config.get("scratch_disk")
            self.scratch_disk = ScratchDiskConfig(
                path=scratch_disk.get("path"),
                type=scratch_disk.get("type"),
                sd_id=scratch_disk.get("domainID"),
                img_id=scratch_disk.get("imageID"),
                vol_id=scratch_disk.get("volumeID"))
        else:
            self.scratch_disk = None


class CheckpointConfig(properties.Owner):
    id = properties.UUID(required=True)
    xml = properties.String()

    def __init__(self, checkpoint_config):
        self.id = checkpoint_config.get("id")
        self.xml = checkpoint_config.get("xml")
        if "config" in checkpoint_config:
            self.config = BackupConfig(checkpoint_config["config"])
        else:
            self.config = None

        if self.config is None and self.xml is None:
            raise exception.CheckpointError(
                reason="Cannot redefine checkpoint without "
                       "checkpoint XML or backup config",
                checkpoint_id=self.id)


class BackupConfig(properties.Owner):

    backup_id = properties.UUID(required=True)
    from_checkpoint_id = properties.UUID(required='')
    to_checkpoint_id = properties.UUID(default='')
    require_consistency = properties.Boolean()
    creation_time = properties.Integer(minval=0)

    def __init__(self, backup_config):
        self.backup_id = backup_config.get("backup_id")
        self.from_checkpoint_id = backup_config.get("from_checkpoint_id")
        self.to_checkpoint_id = backup_config.get("to_checkpoint_id")
        self.require_consistency = backup_config.get("require_consistency")
        self.creation_time = backup_config.get("creation_time")

        self.disks = [DiskConfig(d) for d in backup_config.get("disks", ())]
        for disk in self.disks:
            if (self.from_checkpoint_id is None and
                    disk.backup_mode == MODE_INCREMENTAL):
                raise exception.BackupError(
                    reason="Cannot start an incremental backup for disk, "
                           "full backup is requested",
                    backup=self.backup_id,
                    disk=disk)


def start_backup(vm, dom, config):
    backup_cfg = BackupConfig(config)
    if not backup_cfg.disks:
        raise exception.BackupError(
            reason="Cannot start a backup without disks",
            backup=backup_cfg.backup_id)

    backup_disks = _get_backup_disks(vm, backup_cfg)
    path = socket_path(backup_cfg.backup_id)
    nbd_addr = nbdutils.UnixAddress(path)

    # Create scratch disk for each drive
    _create_scratch_disks(vm, dom, backup_cfg.backup_id, backup_disks)

    try:
        res = vm.freeze()
        if response.is_error(res) and backup_cfg.require_consistency:
            raise exception.BackupError(
                reason="Failed freeze VM: {}".format(res["status"]["message"]),
                vm_id=vm.id,
                backup=backup_cfg)

        backup_xml = create_backup_xml(
            nbd_addr, backup_disks, backup_cfg.from_checkpoint_id)
        checkpoint_xml = create_checkpoint_xml(backup_cfg, backup_disks)

        vm.log.info(
            "Starting backup for backup_id: %r, "
            "backup xml: %s\ncheckpoint xml: %s",
            backup_cfg.backup_id, backup_xml, checkpoint_xml)

        _begin_backup(vm, dom, backup_cfg, backup_xml, checkpoint_xml)
    except:
        # remove all the created scratch disks
        _remove_scratch_disks(vm, backup_cfg.backup_id)
        raise
    finally:
        # Must always thaw, even if freeze failed; in case the guest
        # did freeze the filesystems, but failed to reply in time.
        # Libvirt is using same logic (see src/qemu/qemu_driver.c).
        vm.thaw()

    backup = _get_backup(vm, dom, backup_cfg.backup_id)
    vm.log.debug("backup_id %r info: %s", backup_cfg.backup_id, backup)

    _start_monitoring_scratch_disks(vm, backup_disks, backup)

    return _backup_info(
        vm, dom, backup_cfg.backup_id, backup,
        checkpoint_id=backup_cfg.to_checkpoint_id)


def stop_backup(vm, dom, backup_id):
    if _backup_exists(vm, dom, backup_id):
        try:
            dom.abortJob()
        except libvirt.libvirtError as e:
            if e.get_error_code() != libvirt.VIR_ERR_OPERATION_INVALID:
                raise exception.BackupError(
                    reason="Failed to end VM backup: {}".format(e),
                    vm_id=vm.id,
                    backup_id=backup_id)

    _stop_monitoring_scratch_disks(vm)
    _remove_scratch_disks(vm, backup_id)


def backup_info(vm, dom, backup_id, checkpoint_id=None):
    backup = _get_backup(vm, dom, backup_id)
    vm.log.debug("backup_id %r info: %s", backup_id, backup)
    return _backup_info(
        vm, dom, backup_id, backup, checkpoint_id=checkpoint_id)


def delete_checkpoints(vm, dom, checkpoint_ids):
    deleted_checkpoint_ids = []
    # The engine should send the list of
    # checkpoints ordered from the base to the leaf
    for checkpoint_id in checkpoint_ids:
        vm.log.info("Delete VM %r checkpoint %r", vm.id, checkpoint_id)

        try:
            checkpoint = dom.checkpointLookupByName(checkpoint_id)
            checkpoint.delete()
        except libvirt.libvirtError as e:
            if e.get_error_code() == libvirt.VIR_ERR_NO_DOMAIN_CHECKPOINT:
                vm.log.debug(
                    "Checkpoint_id: %r doesn't exist, error: %s",
                    checkpoint_id, e)

            else:
                vm.log.error(
                    "Failed to delete VM %r checkpoint %r: %s",
                    vm.id, checkpoint_id, e)

                result = {
                    'checkpoint_ids': deleted_checkpoint_ids,
                    'error': {
                        'code': e.get_error_code(),
                        'message': e.get_error_message()
                    }
                }
                return dict(result=result)

        deleted_checkpoint_ids.append(checkpoint_id)

    result = {'checkpoint_ids': deleted_checkpoint_ids}
    return dict(result=result)


def redefine_checkpoints(vm, dom, checkpoints):
    checkpoint_ids = []
    # The engine should send the list of
    # checkpoints ordered from the base to the leaf
    for checkpoint in checkpoints:
        checkpoint_cfg = CheckpointConfig(checkpoint)
        vm.log.info("Redefine VM %r checkpoint %r",
                    vm.id, checkpoint_cfg.id)

        if checkpoint_cfg.config:
            backup_disks = _get_backup_disks(vm, checkpoint_cfg.config)
            checkpoint_xml = create_checkpoint_xml(
                checkpoint_cfg.config, backup_disks)
        else:
            checkpoint_xml = checkpoint_cfg.xml

        flags = (
            libvirt.VIR_DOMAIN_CHECKPOINT_CREATE_REDEFINE |
            libvirt.VIR_DOMAIN_CHECKPOINT_CREATE_REDEFINE_VALIDATE
        )
        try:
            dom.checkpointCreateXML(checkpoint_xml, flags)
        except libvirt.libvirtError as e:
            vm.log.error(
                "Failed to redefine VM %r checkpoint %r: %s",
                vm.id, checkpoint_cfg.id, e)
            result = {
                'checkpoint_ids': checkpoint_ids,
                'error': {
                    'code': e.get_error_code(),
                    'message': e.get_error_message()
                }
            }
            return dict(result=result)

        checkpoint_ids.append(checkpoint_cfg.id)

    result = {'checkpoint_ids': checkpoint_ids}
    return dict(result=result)


def list_checkpoints(vm, dom):
    flags = libvirt.VIR_DOMAIN_CHECKPOINT_LIST_TOPOLOGICAL
    try:
        checkpoints = dom.listAllCheckpoints(flags=flags)
        result = [checkpoint.getName() for checkpoint in checkpoints]
    except libvirt.libvirtError as e:
        raise exception.CheckpointError(
            reason="Failed to fetch defined checkpoints list: {}".format(e),
            vm_id=vm.id)

    return dict(result=result)


def dump_checkpoint(dom, checkpoint_id):
    try:
        checkpoint = dom.checkpointLookupByName(checkpoint_id)
        return dict(result={'checkpoint': checkpoint.getXMLDesc()})
    except libvirt.libvirtError as e:
        if e.get_error_code() == libvirt.VIR_ERR_NO_DOMAIN_CHECKPOINT:
            raise exception.NoSuchCheckpointError(
                reason="Failed to fetch checkpoint: {}".format(e),
                checkpoint_id=checkpoint_id)
        raise


def _get_backup_disks(vm, backup_cfg):
    backup_disks = {}
    try:
        for disk in backup_cfg.disks:
            drive = vm.findDriveByUUIDs({
                'domainID': disk.dom_id,
                'imageID': disk.img_id,
                'volumeID': disk.vol_id})
            backup_disks[disk.img_id] = BackupDisk(
                drive,
                disk.backup_mode,
                disk.scratch_disk)
    except LookupError as e:
        raise exception.BackupError(
            reason="Failed to find one of the backup disks: {}".format(e),
            vm_id=vm.id,
            backup=backup_cfg)

    return backup_disks


def _start_monitoring_scratch_disks(vm, backup_disks, backup):
    disks = backup["disks"]
    for backup_disk in backup_disks.values():
        if backup_disk.scratch_disk.type == DISK_TYPE.BLOCK:
            scratch_disk = disks[backup_disk.drive.name]
            vm.log.info("Start monitoring scratch disk %s for drive %s",
                        scratch_disk, backup_disk.drive.name)
            d = {"index": scratch_disk["index"]}
            if backup_disk.scratch_disk.sd_id is not None:
                d["sd_id"] = backup_disk.scratch_disk.sd_id
            if backup_disk.scratch_disk.img_id is not None:
                d["img_id"] = backup_disk.scratch_disk.img_id
            if backup_disk.scratch_disk.vol_id is not None:
                d["vol_id"] = backup_disk.scratch_disk.vol_id
            backup_disk.drive.scratch_disk = d


def _stop_monitoring_scratch_disks(vm):
    for drive in list(vm.getDiskDevices()):
        if drive.scratch_disk:
            vm.log.info("Stop monitoring scratch disk %s for drive %s",
                        drive.scratch_disk, drive.name)
            drive.scratch_disk = None


def _get_backup(vm, dom, backup_id):
    backup_xml = _get_backup_xml(vm.id, dom, backup_id)
    vm.log.debug("backup_id %r xml: %s", backup_id, backup_xml)
    return _parse_backup_xml(vm, backup_id, backup_xml)


def _backup_info(vm, dom, backup_id, backup, checkpoint_id=None):
    nbd_addr = nbdutils.UnixAddress(backup["socket"])

    backup_urls = {}
    for name, disk in backup["disks"].items():
        drive = vm.find_device_by_name_or_path(name)
        backup_urls[drive.imageID] = nbd_addr.url(disk["exportname"])

    result = {"disks": backup_urls}

    # TODO: Remove this; engine >= 4.4.6 does not need the checkpoint xml, and
    # older engine did not support incremental backup.
    if checkpoint_id is not None:
        _add_checkpoint_xml(vm, dom, backup_id, checkpoint_id, result)

    return dict(result=result)


def _get_backup_xml(vm_id, dom, backup_id):
    try:
        backup_xml = dom.backupGetXMLDesc()
    except libvirt.libvirtError as e:
        if e.get_error_code() == libvirt.VIR_ERR_NO_DOMAIN_BACKUP:
            raise exception.NoSuchBackupError(
                reason="VM backup not exists: {}".format(e),
                vm_id=vm_id,
                backup_id=backup_id)

        raise exception.BackupError(
            reason="Failed to fetch VM ''backup info: {}".format(e),
            vm_id=vm_id,
            backup_id=backup_id)

    return backup_xml


def _backup_exists(vm, dom, backup_id):
    try:
        _get_backup_xml(vm.id, dom, backup_id)
        return True
    except (exception.NoSuchBackupError, virdomain.NotConnectedError) as e:
        vm.log.info(
            "VM with id '%s' or backup with id '%s' not found, error: %s",
            backup_id, vm.id, e)
        return False


def _add_checkpoint_xml(vm, dom, backup_id, checkpoint_id, result):
    try:
        checkpoint = dom.checkpointLookupByName(checkpoint_id)
        result['checkpoint'] = checkpoint.getXMLDesc()
    except libvirt.libvirtError as e:
        if e.get_error_code() == libvirt.VIR_ERR_NO_DOMAIN_CHECKPOINT:
            vm.log.exception(
                "Checkpoint_id: %r for backup_id: %r, doesn't exist, "
                "error: %s", checkpoint_id, backup_id, e)
        else:
            vm.log.exception(
                "Failed to fetch checkpoint_id: %r for backup_id: %r, "
                "error: %s", checkpoint_id, backup_id, e)


def _begin_backup(vm, dom, backup_cfg, backup_xml, checkpoint_xml):
    flags = libvirt.VIR_DOMAIN_BACKUP_BEGIN_REUSE_EXTERNAL
    try:
        dom.backupBegin(backup_xml, checkpoint_xml, flags=flags)
    except libvirt.libvirtError as e:
        if e.get_error_code() == libvirt.VIR_ERR_CHECKPOINT_INCONSISTENT:
            raise exception.InconsistentCheckpointError(
                reason="Checkpoint can't be used: {}".format(e),
                vm_id=vm.id,
                backup=backup_cfg,
                checkpoint_xml=checkpoint_xml)

        raise exception.BackupError(
            reason="Error starting backup: {}".format(e),
            vm_id=vm.id,
            backup=backup_cfg)


def _parse_backup_xml(vm, backup_id, backup_xml):
    """
    Parse the interesting parts from backup xml to dict.

    Input:

    <domainbackup mode='pull' id='1'>
      <incremental>checkpoint-name</incremental>
      <server transport='unix' socket='/socket'/>
      <disks>
        <disk name='vda' backup='yes' type='file' index='7'>
          <driver type='qcow2'/>
          <scratch file='/scratch1.qcow2'/>
        </disk>
        <disk name='sda' backup='yes' type='file' index='8'>
          <driver type='qcow2'/>
          <scratch file='/scratch2.qcow2'/>
        </disk>
      </disks>
    </domainbackup>

    Output:

    {
        "incremental": "checkpoint-name",
        "socket": "/socket",
        "disks": {
            "vda": {
                "index": 7,
                "exportname": "vda",
                "type": "file",
            },
            "sda": {
                "index": 8,
                "exportname": "sda",
                "type": "file",
            },
    }
    """
    backup = {}

    domainbackup = xmlutils.fromstring(backup_xml)

    incremental = domainbackup.find("./incremental")
    if incremental is not None:
        backup["incremental"] = incremental.text

    server = domainbackup.find('./server')
    if server is None:
        _raise_parse_error(vm.id, backup_id, backup_xml)

    socket = server.get('socket')
    if socket is None:
        _raise_parse_error(vm.id, backup_id, backup_xml)

    backup["socket"] = socket

    disks = {}
    for disk in domainbackup.findall("./disks/disk[@backup='yes']"):
        disk_name = disk.get('name')
        if disk_name is None:
            _raise_parse_error(vm.id, backup_id, backup_xml)

        index = disk.get("index")
        if index is None:
            _raise_parse_error(vm.id, backup_id, backup_xml)

        try:
            index = int(index)
        except ValueError:
            _raise_parse_error(vm.id, backup_id, backup_xml)

        exportname = disk.get("exportname")
        if exportname is None:
            _raise_parse_error(vm.id, backup_id, backup_xml)

        disk_type = disk.get("type")
        if disk_type is None:
            _raise_parse_error(vm.id, backup_id, backup_xml)

        disks[disk_name] = {
            "index": index,
            "exportname": exportname,
            "type": disk_type,
        }

    backup["disks"] = disks
    return backup


def _raise_parse_error(vm_id, backup_id, backup_xml):
    raise exception.BackupError(
        reason="Failed to parse invalid libvirt "
               "backup XML: {}".format(backup_xml),
        vm_id=vm_id,
        backup_id=backup_id)


def create_backup_xml(address, backup_disks, from_checkpoint_id=None):
    domainbackup = vmxml.Element('domainbackup', mode='pull')

    if from_checkpoint_id is not None:
        incremental = vmxml.Element('incremental')
        incremental.appendTextNode(from_checkpoint_id)
        domainbackup.appendChild(incremental)

    server = vmxml.Element(
        'server', transport=address.transport, socket=address.path)

    domainbackup.appendChild(server)

    disks = vmxml.Element('disks')

    # fill the backup XML disks
    for backup_disk in backup_disks.values():
        disk = vmxml.Element(
            'disk',
            name=backup_disk.drive.name,
            type=backup_disk.scratch_disk.type)

        # If backup mode reported by the engine it should be added
        # to the backup XML.
        if backup_disk.backup_mode is not None:
            vmxml.set_attr(disk, "backupmode", backup_disk.backup_mode)

            if backup_disk.backup_mode == MODE_INCREMENTAL:
                # if backupmode is 'incremental' we should also provide the
                # checkpoint ID we start the incremental backup from.
                vmxml.set_attr(disk, MODE_INCREMENTAL, from_checkpoint_id)

        # scratch element can have dev=/path/to/block/disk
        # or file=/path/to/file/disk attribute according to
        # the disk type.
        if backup_disk.scratch_disk.type == DISK_TYPE.BLOCK:
            scratch = vmxml.Element(
                'scratch', dev=backup_disk.scratch_disk.path)
        else:
            scratch = vmxml.Element(
                'scratch', file=backup_disk.scratch_disk.path)

        storage.disable_dynamic_ownership(scratch, write_type=False)
        disk.appendChild(scratch)

        disks.appendChild(disk)

    domainbackup.appendChild(disks)

    return xmlutils.tostring(domainbackup)


def create_checkpoint_xml(backup_cfg, backup_disks):
    if backup_cfg.to_checkpoint_id is None:
        return None

    # create the checkpoint XML for a backup
    checkpoint = vmxml.Element('domaincheckpoint')

    name = vmxml.Element('name')
    name.appendTextNode(backup_cfg.to_checkpoint_id)
    checkpoint.appendChild(name)

    cp_description = "checkpoint for backup '{}'".format(
        backup_cfg.backup_id)
    description = vmxml.Element('description')
    description.appendTextNode(cp_description)
    checkpoint.appendChild(description)

    if backup_cfg.creation_time:
        creation_time = vmxml.Element('creationTime')
        creation_time.appendTextNode(str(backup_cfg.creation_time))
        checkpoint.appendChild(creation_time)

    # When the XML is created for redefining a checkpoint,
    # the checkpoint may not contain disks at all, for e.g -
    # old disks that were removed/detached from the VM.
    # In that case, we should not add the <disks> element.
    if backup_cfg.disks:
        disks = vmxml.Element('disks')
        for disk in backup_cfg.disks:
            if disk.checkpoint:
                backup_disk = backup_disks[disk.img_id]
                disk_elm = vmxml.Element(
                    'disk',
                    name=backup_disk.drive.name,
                    checkpoint='bitmap',
                    bitmap=backup_cfg.to_checkpoint_id)
                disks.appendChild(disk_elm)

        checkpoint.appendChild(disks)

    return xmlutils.tostring(checkpoint)


def socket_path(backup_id):
    # TODO: We need to create a vm directory in
    # /run/vdsm/backup for each vm backup socket.
    # This way we can prevent vms from accessing
    # other vms backup socket with selinux.
    return os.path.join(P_BACKUP, backup_id)


def _create_scratch_disks(vm, dom, backup_id, backup_disks):
    for backup_disk in backup_disks.values():
        # Skip the scratch disk creation if the scratch
        # disk already created by the engine.
        if backup_disk.scratch_disk is not None:
            continue

        try:
            path = _create_transient_disk(
                vm, dom, backup_id, backup_disk.drive)
        except Exception:
            _remove_scratch_disks(vm, backup_id)
            raise
        backup_disk.scratch_disk = ScratchDiskConfig(path=path, type="file")


def _remove_scratch_disks(vm, backup_id):
    log.info(
        "Removing scratch disks for backup id: %s", backup_id)

    res = vm.cif.irs.list_transient_disks(vm.id)
    if response.is_error(res):
        raise exception.BackupError(
            reason="Failed to fetch scratch disks: {}".format(res),
            vm_id=vm.id,
            backup_id=backup_id)

    for disk_name in res['result']:
        res = vm.cif.irs.remove_transient_disk(vm.id, disk_name)
        if response.is_error(res):
            log.error(
                "Failed to remove backup '%s' "
                "scratch disk for drive name: %s, ",
                backup_id, disk_name)


def _get_drive_capacity(dom, drive):
    try:
        capacity, _, _ = dom.blockInfo(drive.path)
        return capacity
    except libvirt.libvirtError as e:
        raise exception.BackupError(
            reason="Failed to get drive {} capacity: {}".format(
                drive.name, e))


def _create_transient_disk(vm, dom, backup_id, drive):
    disk_name = "{}.{}".format(backup_id, drive.name)
    drive_size = _get_drive_capacity(dom, drive)

    res = vm.cif.irs.create_transient_disk(
        owner_name=vm.id,
        disk_name=disk_name,
        size=drive_size,
    )
    if response.is_error(res):
        raise exception.BackupError(
            reason='Failed to create transient disk: {}'.format(res),
            vm_id=vm.id,
            backup_id=backup_id,
            drive_name=drive.name)
    return res['result']['path']
