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

from __future__ import absolute_import
from __future__ import division

import functools
import libvirt
import logging
import os
import six

from vdsm.common import exception
from vdsm.common import nbdutils
from vdsm.common import properties
from vdsm.common import response
from vdsm.common import xmlutils
from vdsm.common.constants import P_BACKUP

from vdsm.virt import virdomain
from vdsm.virt import vmxml
from vdsm.virt.vmdevices import storage

log = logging.getLogger("storage.backup")

# DomainAdapter should be defined only if libvirt supports
# incremental backup API
backup_enabled = hasattr(libvirt.virDomain, "backupBegin")


def requires_libvirt_support():
    """
    Decorator for prevent using backup methods to be
    called if libvirt doesn't supports incremental backup.
    """
    def decorator(f):
        @functools.wraps(f)
        def wrapper(*a, **kw):
            if not backup_enabled:
                raise exception.UnsupportedOperation(
                    "Libvirt version doesn't support "
                    "incremental backup operations"
                )
            return f(*a, **kw)
        return wrapper
    return decorator


if backup_enabled:
    @virdomain.expose(
        "backupBegin",
        "abortJob",
        "backupGetXMLDesc",
        "checkpointLookupByName",
        "blockInfo"
    )
    class DomainAdapter(object):
        """
        VM wrapper class that exposes only
        libvirt backup related operations.
        """
        def __init__(self, vm):
            self._vm = vm


class DiskConfig(properties.Owner):
    vol_id = properties.UUID(required=True)
    img_id = properties.UUID(required=True)
    dom_id = properties.UUID(required=True)
    checkpoint = properties.Boolean(required=True)

    def __init__(self, disk_config):
        self.vol_id = disk_config.get("volumeID")
        self.img_id = disk_config.get("imageID")
        self.dom_id = disk_config.get("domainID")
        # Mark if the disk is included in the checkpoint.
        self.checkpoint = disk_config.get("checkpoint")


class BackupConfig(properties.Owner):

    backup_id = properties.UUID(required=True)
    from_checkpoint_id = properties.UUID(required='')
    to_checkpoint_id = properties.UUID(default='')

    def __init__(self, backup_config):
        self.backup_id = backup_config.get("backup_id")
        self.from_checkpoint_id = backup_config.get("from_checkpoint_id")
        self.to_checkpoint_id = backup_config.get("to_checkpoint_id")
        self.disks = [DiskConfig(d) for d in backup_config.get("disks", ())]
        if len(self.disks) == 0:
            raise exception.BackupError(
                reason="Cannot start a backup without disks",
                backup=self.backup_id)


def start_backup(vm, dom, config):
    backup_cfg = BackupConfig(config)

    if backup_cfg.from_checkpoint_id is not None:
        raise exception.BackupError(
            reason="Incremental backup not supported yet",
            vm_id=vm.id,
            backup=backup_cfg)

    try:
        drives = _get_disks_drives(vm, backup_cfg.disks)
    except LookupError as e:
        raise exception.BackupError(
            reason="Failed to find one of the backup disks: {}".format(e),
            vm_id=vm.id,
            backup=backup_cfg)

    path = socket_path(backup_cfg.backup_id)
    nbd_addr = nbdutils.UnixAddress(path)

    # Create scratch disk for each drive
    scratch_disks = _create_scratch_disks(
        vm, dom, backup_cfg.backup_id, drives)

    try:
        vm.freeze()
        backup_xml = create_backup_xml(nbd_addr, drives, scratch_disks)
        checkpoint_xml = create_checkpoint_xml(backup_cfg, drives)

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

    disks_urls = {
        img_id: nbd_addr.url(drive.name)
        for img_id, drive in six.iteritems(drives)}

    result = {'disks': disks_urls}

    if backup_cfg.to_checkpoint_id is not None:
        _add_checkpoint_xml(
            vm, dom, backup_cfg.backup_id, backup_cfg.to_checkpoint_id, result)

    return dict(result=result)


def stop_backup(vm, dom, backup_id):
    try:
        _get_backup_xml(vm.id, dom, backup_id)
    except exception.NoSuchBackupError:
        vm.log.info(
            "No backup with id '%s' found for vm '%s'",
            backup_id, vm.id)
        _remove_scratch_disks(vm, backup_id)
        return

    try:
        dom.abortJob()
    except libvirt.libvirtError as e:
        if e.get_error_code() != libvirt.VIR_ERR_OPERATION_INVALID:
            raise exception.BackupError(
                reason="Failed to end VM backup: {}".format(e),
                vm_id=vm.id,
                backup_id=backup_id)

    _remove_scratch_disks(vm, backup_id)


def backup_info(vm, dom, backup_id, checkpoint_id=None):
    backup_xml = _get_backup_xml(vm.id, dom, backup_id)
    vm.log.debug("backup_id %r info: %s", backup_id, backup_xml)

    disks_urls = _parse_backup_info(vm, backup_id, backup_xml)
    result = {'disks': disks_urls}

    if checkpoint_id is not None:
        _add_checkpoint_xml(vm, dom, backup_id, checkpoint_id, result)

    return dict(result=result)


def delete_checkpoints(vm, dom, checkpoint_ids):
    raise exception.MethodNotImplemented()


def redefine_checkpoints(vm, dom, checkpoints):
    raise exception.MethodNotImplemented()


def _get_disks_drives(vm, disks_cfg):
    drives = {}
    for disk in disks_cfg:
        drive = vm.findDriveByUUIDs({
            'domainID': disk.dom_id,
            'imageID': disk.img_id,
            'volumeID': disk.vol_id})
        drives[disk.img_id] = drive
    return drives


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
    # pylint: disable=no-member
    flags = libvirt.VIR_DOMAIN_BACKUP_BEGIN_REUSE_EXTERNAL
    try:
        dom.backupBegin(backup_xml, checkpoint_xml, flags=flags)
    except libvirt.libvirtError as e:
        raise exception.BackupError(
            reason="Error starting backup: {}".format(e),
            vm_id=vm.id,
            backup=backup_cfg)


def _parse_backup_info(vm, backup_id, backup_xml):
    """
    Parse the backup info returned XML,
    For example using Unix socket:

    <domainbackup mode='pull' id='1'>
        <server transport='unix' socket='/run/vdsm/backup-id'/>
        <disks>
            <disk name='vda' backup='yes' type='file'>
                <driver type='qcow2'/>
                <scratch file='/path/to/scratch/disk.qcow2'/>
            </disk>
            <disk name='sda' backup='yes' type='file'>
                <driver type='qcow2'/>
                <scratch file='/path/to/scratch/disk.qcow2'/>
            </disk>
        </disks>
    </domainbackup>
    """
    domainbackup = xmlutils.fromstring(backup_xml)

    server = domainbackup.find('./server')
    if server is None:
        _raise_parse_error(vm.id, backup_id, backup_xml)

    path = server.get('socket')
    if path is None:
        _raise_parse_error(vm.id, backup_id, backup_xml)

    address = nbdutils.UnixAddress(path)

    disks_urls = {}
    for disk in domainbackup.findall("./disks/disk[@backup='yes']"):
        disk_name = disk.get('name')
        if disk_name is None:
            _raise_parse_error(vm.id, backup_id, backup_xml)
        drive = vm.find_device_by_name_or_path(disk_name)
        disks_urls[drive.imageID] = address.url(disk_name)

    return disks_urls


def _raise_parse_error(vm_id, backup_id, backup_xml):
    raise exception.BackupError(
        reason="Failed to parse invalid libvirt "
               "backup XML: {}".format(backup_xml),
        vm_id=vm_id,
        backup_id=backup_id)


def create_backup_xml(address, drives, scratch_disks):
    domainbackup = vmxml.Element('domainbackup', mode='pull')

    server = vmxml.Element(
        'server', transport=address.transport, socket=address.path)

    domainbackup.appendChild(server)

    disks = vmxml.Element('disks')

    # fill the backup XML disks
    for drive in drives.values():
        disk = vmxml.Element('disk', name=drive.name, type='file')
        # scratch element can have dev=/path/to/block/disk
        # or file=/path/to/file/disk attribute according to
        # the disk type.
        # Currently, all the scratch disks resides on the
        # host local file storage.
        scratch = vmxml.Element('scratch', file=scratch_disks[drive.name])

        storage.disable_dynamic_ownership(scratch, write_type=False)
        disk.appendChild(scratch)

        disks.appendChild(disk)

    domainbackup.appendChild(disks)

    return xmlutils.tostring(domainbackup)


def create_checkpoint_xml(backup_cfg, drives):
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

    if backup_cfg.from_checkpoint_id is not None:
        cp_parent = vmxml.Element('parent')
        parent_name = vmxml.Element('name')
        parent_name.appendTextNode(backup_cfg.from_checkpoint_id)
        cp_parent.appendChild(parent_name)
        checkpoint.appendChild(cp_parent)

    disks = vmxml.Element('disks')
    for disk in backup_cfg.disks:
        if disk.checkpoint:
            drive = drives[disk.img_id]
            disk_elm = vmxml.Element(
                'disk', name=drive.name, checkpoint='bitmap')
            disks.appendChild(disk_elm)

    checkpoint.appendChild(disks)

    return xmlutils.tostring(checkpoint)


def socket_path(backup_id):
    # TODO: We need to create a vm directory in
    # /run/vdsm/backup for each vm backup socket.
    # This way we can prevent vms from accessing
    # other vms backup socket with selinux.
    return os.path.join(P_BACKUP, backup_id)


def _create_scratch_disks(vm, dom, backup_id, drives):
    scratch_disks = {}

    for drive in drives.values():
        try:
            path = _create_transient_disk(vm, dom, backup_id, drive)
        except Exception:
            _remove_scratch_disks(vm, backup_id)
            raise

        scratch_disks[drive.name] = path

    return scratch_disks


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
        size=drive_size
    )
    if response.is_error(res):
        raise exception.BackupError(
            reason='Failed to create transient disk: {}'.format(res),
            vm_id=vm.id,
            backup_id=backup_id,
            drive_name=drive.name)
    return res['result']['path']
