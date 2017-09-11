#
# Copyright 2008-2017 Red Hat, Inc.
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
# pylint: disable=no-member

from __future__ import absolute_import

import collections
import os
import xml.etree.ElementTree as ET

from vdsm.common import base26
from vdsm.common import conv
from vdsm.common import errors
from vdsm.common import exception
from vdsm.config import config
from vdsm import constants
from vdsm import cpuarch
from vdsm import utils
from vdsm.virt import vmtune
from vdsm.virt import vmxml

from . import core
from . import hwclass
from . import lease

DEFAULT_INTERFACE_FOR_ARCH = {
    cpuarch.X86_64: 'ide',
    cpuarch.PPC64: 'scsi',
    cpuarch.PPC64LE: 'scsi'
}


class DISK_TYPE:
    BLOCK = "block"
    NETWORK = "network"
    FILE = "file"


SOURCE_ATTR = {
    DISK_TYPE.FILE: 'file',
    DISK_TYPE.NETWORK: 'name',
    DISK_TYPE.BLOCK: 'dev'
}


class DRIVE_SHARED_TYPE:
    NONE = "none"
    EXCLUSIVE = "exclusive"
    SHARED = "shared"
    TRANSIENT = "transient"

    @classmethod
    def getAllValues(cls):
        # TODO: use introspection
        return (cls.NONE, cls.EXCLUSIVE, cls.SHARED, cls.TRANSIENT)


class VolumeNotFound(errors.Base):
    msg = ("Cannot find volume {self.vol_id} in drive {self.drive_name}'s "
           "volume chain")

    def __init__(self, drive_name, vol_id):
        self.drive_name = drive_name
        self.vol_id = vol_id


class InvalidBackingStoreIndex(errors.Base):
    msg = ("Backing store for path {self.path} "
           "contains invalid index {self.index!r}")

    def __init__(self, path, index):
        self.path = path
        self.index = index


VolumeChainEntry = collections.namedtuple(
    'VolumeChainEntry',
    ['uuid', 'path', 'allocation', 'index'])


BlockInfo = collections.namedtuple("BlockInfo", [
    "capacity",  # guest virtual size
    "allocation",  # host allocated size (highest allocated offset)
    "physical"  # host physical size (lv size, file size)
])


class Drive(core.Base):
    __slots__ = ('iface', '_path', 'readonly', 'bootOrder', 'domainID',
                 'poolID', 'imageID', 'UUID', 'volumeID', 'format',
                 'propagateErrors', 'address', 'apparentsize', 'volumeInfo',
                 'index', 'name', 'optional', 'shared', 'truesize',
                 'volumeChain', 'baseVolumeID', 'serial', 'reqsize', 'cache',
                 'extSharedState', 'drv', 'sgio', 'GUID', 'diskReplicate',
                 '_diskType', 'hosts', 'protocol', 'auth', 'discard',
                 'vm_custom', 'blockinfo')
    VOLWM_CHUNK_SIZE = (config.getint('irs', 'volume_utilization_chunk_mb') *
                        constants.MEGAB)
    VOLWM_FREE_PCT = 100 - config.getint('irs', 'volume_utilization_percent')
    VOLWM_CHUNK_REPLICATE_MULT = 2  # Chunk multiplier during replication

    # Estimate of the additional space needed for qcow format internal data.
    VOLWM_COW_OVERHEAD = 1.1

    @classmethod
    def update_device_info(cls, vm, device_conf):
        # FIXME!  We need to gather as much info as possible from the libvirt.
        # In the future we can return this real data to management instead of
        # vm's conf
        for x in vm.domain.get_device_elements('disk'):
            alias, devPath, name = _get_drive_identification(x)
            readonly = vmxml.find_first(x, 'readonly', None) is not None
            bootOrder = vmxml.find_attr(x, 'boot', 'order')

            devType = vmxml.attr(x, 'device')
            if devType == 'disk':
                # raw/qcow2
                drv = vmxml.find_attr(x, 'driver', 'type')
            else:
                drv = 'raw'
            # Get disk address
            address = vmxml.device_address(x)

            # Keep data as dict for easier debugging
            deviceDict = {'path': devPath, 'name': name,
                          'readonly': readonly, 'bootOrder': bootOrder,
                          'address': address, 'type': devType}

            # display indexed pairs of ordered values from 2 dicts
            # such as {key_1: (valueA_1, valueB_1), ...}
            def mergeDicts(deviceDef, dev):
                return dict((k, (deviceDef[k], getattr(dev, k, None)))
                            for k in deviceDef.iterkeys())

            vm.log.debug('Looking for drive with attributes %s', deviceDict)
            for d in device_conf:
                # When we analyze a disk device that was already discovered in
                # the past (generally as soon as the VM is created) we should
                # verify that the cached path is the one used in libvirt.
                # We already hit few times the problem that after a live
                # migration the paths were not in sync anymore (BZ#1059482).
                if (hasattr(d, 'alias') and d.alias == alias and
                        d.path != devPath):
                    vm.log.warning('updating drive %s path from %s to %s',
                                   d.alias, d.path, devPath)
                    d.path = devPath
                if d.path == devPath:
                    d.name = name
                    d.type = devType
                    d.drv = drv
                    d.alias = alias
                    d.address = address
                    d.readonly = readonly
                    if bootOrder:
                        d.bootOrder = bootOrder
                    vm.log.debug('Matched %s', mergeDicts(deviceDict, d))
            # Update vm's conf with address for known disk devices
            knownDev = False
            for dev in vm.conf['devices']:
                # See comment in previous loop. This part is used to update
                # the vm configuration as well.
                if ('alias' in dev and dev['alias'] == alias and
                        dev['path'] != devPath):
                    vm.log.warning('updating drive %s config path from %s '
                                   'to %s', dev['alias'], dev['path'],
                                   devPath)
                    dev['path'] = devPath
                if (dev['type'] == hwclass.DISK and
                        dev['path'] == devPath):
                    dev['name'] = name
                    dev['address'] = address
                    dev['alias'] = alias
                    dev['readonly'] = str(readonly)
                    if bootOrder:
                        dev['bootOrder'] = bootOrder
                    vm.log.debug('Matched %s', mergeDicts(deviceDict, dev))
                    knownDev = True
            # Add unknown disk device to vm's conf
            if not knownDev:
                archIface = DEFAULT_INTERFACE_FOR_ARCH[vm.arch]
                iface = archIface if address['type'] == 'drive' else 'pci'
                diskDev = {'type': hwclass.DISK, 'device': devType,
                           'iface': iface, 'path': devPath, 'name': name,
                           'address': address, 'alias': alias,
                           'readonly': str(readonly)}
                if bootOrder:
                    diskDev['bootOrder'] = bootOrder
                vm.log.warn('Found unknown drive: %s', diskDev)
                vm.conf['devices'].append(diskDev)

    def __init__(self, log, **kwargs):
        if not kwargs.get('serial'):
            self.serial = kwargs.get('imageID'[-20:]) or ''
        self._path = None
        self._diskType = None
        # device needs to be initialized in prior
        # cause diskType setter uses self.device
        # in diskType validation
        self.device = kwargs.get('device', 'disk')
        super(Drive, self).__init__(log, **kwargs)
        if not hasattr(self, 'vm_custom'):
            self.vm_custom = {}
        # Keep sizes as int
        self.reqsize = int(kwargs.get('reqsize', '0'))  # Backward compatible
        self.truesize = int(kwargs.get('truesize', '0'))
        self.apparentsize = int(kwargs.get('apparentsize', '0'))
        self.name = makeName(self.iface, self.index)
        self.cache = config.get('vars', 'qemu_drive_cache')
        self.discard = kwargs.get('discard', False)

        # Engine can send 'true' and 'false' as strings
        # floppies are used only internally for sysprep, so
        # they are readonly unless explicitely stated otherwise
        self.readonly = conv.tobool(
            kwargs.get('readonly', self.device == 'floppy'))

        # Used for chunked drives or drives replicating to chunked replica.
        self.blockinfo = None

        self._customize()
        self._setExtSharedState()

    def _setExtSharedState(self):
        # We cannot use tobool here as shared can take several values
        # (e.g. none, exclusive) that would be all mapped to False.
        shared = str(getattr(self, "shared", "false")).lower()

        # Backward compatibility with the old values (true, false)
        if shared == 'true':
            self.extSharedState = DRIVE_SHARED_TYPE.SHARED
        elif shared == 'false':
            if config.getboolean('irs', 'use_volume_leases'):
                self.extSharedState = DRIVE_SHARED_TYPE.EXCLUSIVE
            else:
                self.extSharedState = DRIVE_SHARED_TYPE.NONE
        elif shared in DRIVE_SHARED_TYPE.getAllValues():
            self.extSharedState = shared
        else:
            raise ValueError("Unknown shared value %s" % shared)

    @property
    def hasVolumeLeases(self):
        if self.extSharedState != DRIVE_SHARED_TYPE.EXCLUSIVE:
            return False

        for volInfo in getattr(self, "volumeChain", []):
            if "leasePath" in volInfo and "leaseOffset" in volInfo:
                return True

        return False

    def __getitem__(self, key):
        try:
            value = getattr(self, str(key))
        except AttributeError:
            raise KeyError(key)
        else:
            return value

    def __contains__(self, attr):
        return hasattr(self, attr)

    def isDiskReplicationInProgress(self):
        return hasattr(self, "diskReplicate")

    @property
    def volExtensionChunk(self):
        """
        Returns the volume extension chunk size in bytes.

        This size is used for the thin provisioning on block devices. The value
        is based on the vdsm configuration but can also dynamically change
        according to the VM needs (e.g. increase during a live storage
        migration).
        """
        if self.isDiskReplicationInProgress():
            return self.VOLWM_CHUNK_SIZE * self.VOLWM_CHUNK_REPLICATE_MULT
        return self.VOLWM_CHUNK_SIZE

    @property
    def watermarkLimit(self):
        """
        Returns the watermark limit in bytes.

        When the LV usage reaches this limit an extension is in order (thin
        provisioning on block devices).
        """
        return self.VOLWM_FREE_PCT * self.volExtensionChunk / 100

    def getNextVolumeSize(self, curSize, capacity):
        """
        Returns the next volume size in bytes. This value is based on the
        volExtensionChunk property and it's the size that should be requested
        for the next LV extension.  curSize is the current size of the volume
        to be extended.  For the leaf volume curSize == self.apparentsize.
        For internal volumes it is discovered by calling irs.getVolumeSize().
        capacity is the maximum size of the volume. It can be discovered using
        libvirt.virDomain.blockInfo() or qemuimg.info().
        """
        nextSize = utils.round(curSize + self.volExtensionChunk,
                               constants.MEGAB)
        return min(nextSize, self.getMaxVolumeSize(capacity))

    def getMaxVolumeSize(self, capacity):
        """
        Returns the maximum volume size in bytes. This value is larger than
        drive capacity since we must allocate extra space for cow internal
        data. The actual lv size may be larger due to rounding to next lvm
        extent.
        """
        return utils.round(capacity * self.VOLWM_COW_OVERHEAD,
                           constants.MEGAB)

    @property
    def chunked(self):
        """
        Return True if drive is using chunks and may require extending.

        If a drive is chunked, current drive write watermark and
        Drive.volExtensionChunk is used to detect if a drive should be
        extended, and getNextVolumeSize to find the new size.
        """
        return self.diskType == DISK_TYPE.BLOCK and self.format == "cow"

    @property
    def replicaChunked(self):
        """
        Return True if drive is replicating to chuked storage and the replica
        volume may require extending. See Drive.chunkd for more info.
        """
        replica = getattr(self, "diskReplicate", {})
        return (replica.get("diskType") == DISK_TYPE.BLOCK and
                replica.get("format") == "cow")

    @property
    def path(self):
        return self._path

    @path.setter
    def path(self, path):
        if self._path is not None and self._path != path:
            self.log.debug("Drive %s moved from %r to %r",
                           self.name, self._path, path)
        self._path = path

    @property
    def diskType(self):
        """
        Return diskType sent by engine, or try to check the disk type by
        looking at storage.

        If storage is not available at this point, this will fail with OSError,
        and diskType will remain undefined. Drive cannot be functional at this
        state until storage becomes available, and diskType initialized.
        """
        if self._diskType is None:
            if (self.device in ("cdrom", "floppy") or not
                    utils.isBlockDevice(self.path)):
                self.diskType = DISK_TYPE.FILE
            else:
                self.diskType = DISK_TYPE.BLOCK

        return self._diskType

    @diskType.setter
    def diskType(self, value):
        if value not in SOURCE_ATTR:
            raise exception.UnsupportedOperation(
                "Unsupported diskType %r" % value)
        if self.device in ['cdrom', 'floppy'] and value != DISK_TYPE.FILE:
            raise exception.UnsupportedOperation(
                "diskType of %r can only be 'file'" % self.device)

        if self._diskType is not None and self._diskType != value:
            self.log.debug("Drive %s type changed from %r to %r",
                           self.name, self._diskType, value)

        self._diskType = value

    @property
    def transientDisk(self):
        # Using getattr to handle legacy and removable drives.
        return getattr(self, 'shared', None) == DRIVE_SHARED_TYPE.TRANSIENT

    def _customize(self):
        if self.transientDisk:
            # Force the cache to be writethrough, which is qemu's default.
            # This is done to ensure that we don't ever use cache=none for
            # transient disks, since we create them in /var/run/vdsm which
            # may end up on tmpfs and don't support O_DIRECT, and qemu uses
            # O_DIRECT when cache=none and hence hotplug might fail with
            # error that one can take eternity to debug the reason behind it!
            self.cache = "writethrough"
        elif self.iface == 'virtio':
            try:
                self.cache = self.vm_custom['viodiskcache']
            except KeyError:
                pass  # Ignore if custom disk cache is missing

    def getLeasesXML(self):
        """
        Get lease device elements for drive leases.

        See `.lease.Device.getXML` for more info.

        :returns: generator of `..vmxml.Element` instances
        """
        if not self.hasVolumeLeases:
            return  # empty items generator

        # NOTE: at the moment we are generating the lease only for the leaf,
        # when libvirt will support shared leases this will loop over all the
        # volumes
        for volInfo in self.volumeChain[-1:]:
            device = lease.Device(self.log,
                                  lease_id=volInfo['volumeID'],
                                  sd_id=volInfo['domainID'],
                                  path=volInfo['leasePath'],
                                  offset=volInfo['leaseOffset'])
            yield device.getXML()

    def getXML(self):
        """
        Create domxml for disk/cdrom/floppy.

        <disk type='file' device='disk' snapshot='no'>
          <driver name='qemu' type='qcow2' cache='none'/>
          <source file='/path/to/image'/>
          <target dev='hda' bus='ide'/>
          <serial>54-a672-23e5b495a9ea</serial>
        </disk>
        """
        self._validate()
        diskelem = self.createXmlElem('disk', self.diskType,
                                      ['device', 'address', 'sgio'])
        diskelem.setAttrs(snapshot='no')

        diskelem.appendChild(_getSourceXML(self))

        if self.diskType == DISK_TYPE.NETWORK and hasattr(self, 'auth'):
            diskelem.appendChild(self._getAuthXML())

        diskelem.appendChild(self._getTargetXML())

        if self.extSharedState == DRIVE_SHARED_TYPE.SHARED:
            diskelem.appendChildWithArgs('shareable')

        if self.readonly:
            diskelem.appendChildWithArgs('readonly')

        if getattr(self, 'serial', False) and self.device != 'lun':
            diskelem.appendChildWithArgs('serial', text=self.serial)

        if hasattr(self, 'bootOrder'):
            diskelem.appendChildWithArgs('boot', order=self.bootOrder)

        if self.device == 'disk' or self.device == 'lun':
            diskelem.appendChild(_getDriverXML(self))

        if self.iotune:
            diskelem.appendChild(self._getIotuneXML())

        return diskelem

    def get_extra_xmls(self):
        for elem in self.getLeasesXML():
            yield elem

    def getReplicaXML(self):
        disk = vmxml.Element(
            "disk",
            device=self.diskReplicate["device"],
            snapshot="no",
            type=self.diskReplicate["diskType"],
        )
        disk.appendChild(_getSourceXML(self.diskReplicate))
        disk.appendChild(_getDriverXML(self.diskReplicate))
        return disk

    def _getAuthXML(self):
        auth = vmxml.Element("auth", username=self.auth["username"])
        auth.appendChildWithArgs("secret",
                                 type=self.auth["type"],
                                 uuid=self.auth["uuid"])
        return auth

    def _getTargetXML(self):
        target = vmxml.Element('target', dev=self.name)
        if self.iface:
            target.setAttrs(bus=self.iface)
        return target

    def _getIotuneXML(self):
        iotune = vmxml.Element('iotune')
        for key, value in sorted(self.iotune.items()):
            iotune.appendChildWithArgs(key, text=str(value))
        return iotune

    def _validate(self):
        if self.diskType == DISK_TYPE.NETWORK:
            if not getattr(self, 'hosts', None):
                raise ValueError("Network disk without hosts")
            if not getattr(self, 'protocol', None):
                raise ValueError("Network disk without protocol")

        if self.device != 'lun' and hasattr(self, 'sgio'):
            raise ValueError("sgio attribute can be set only for LUN devices")

        if self.device == 'lun' and self.format == 'cow':
            raise ValueError("cow format is not supported for LUN devices")

    @property
    def _xpath(self):
        """
        Returns xpath to the device in libvirt dom xml
        The path is relative to the root element
        """
        source_key = {
            DISK_TYPE.FILE: 'file',
            DISK_TYPE.BLOCK: 'dev',
            DISK_TYPE.NETWORK: 'name',
        }
        return ("./devices/disk/source[@%s='%s']" %
                (source_key[self.diskType], self.path))

    def is_attached_to(self, xml_string):
        dom = ET.fromstring(xml_string)
        return bool(dom.findall(self._xpath))

    def __repr__(self):
        return ("<Drive name={self.name}, type={self.diskType}, "
                "path={self.path} "
                "at {addr:#x}>").format(self=self, addr=id(self))

    @property
    def iotune(self):
        return self.specParams.get('ioTune', {}).copy()

    @iotune.setter
    def iotune(self, value):
        iotune = value.copy()
        vmtune.validate_io_tune_params(iotune)
        self.specParams['ioTune'] = iotune

    def volume_target(self, vol_id, actual_chain):
        """
        Retrieves volume's device target
        from drive's volume chain using its ID.
        That device target is used in block-commit api of libvirt.

        Arguments:
            vol_id (str): Volume's UUID
            actual_chain (VolumeChainEntry[]): Current volume chain
                as parsed from libvirt xml,
                see parse_volume_chain. We expect it to be
                ordered from base to top.

        Returns:
            str: Volume device target - None for top volume,
                "vda[1]" for the next volume after top and so on.

        Raises:
            VolumeNotFound exception when volume is not in chain.
        """
        for v in self.volumeChain:
            if v['volumeID'] == vol_id:
                index = chain_index(actual_chain, vol_id, self.name)

                # libvirt device target format is name[index] where name is
                # target device name inside a vm and index is a number,
                # pointing to a snapshot layer.
                # Unfortunately, top layer do not have index value and libvirt
                # doesn't support referencing top layer as name[0] therefore,
                # we have to check for index absence and return just name for
                # the top layer. We have an RFE for that problem,
                # https://bugzilla.redhat.com/1451398 and when it will be
                # implemented, we need to remove special handling of
                # the active layer.
                if index is None:
                    # As right now libvirt is not able to correctly parse
                    # 'name' as a reference to the active layer we need to
                    # return None, so libvirt will use active layer as a
                    # default value for None. We have bug filed for that issue:
                    # https://bugzilla.redhat.com/1451394 and we need to return
                    # self.name instead of None when it is fixed.
                    return None
                else:
                    return "%s[%d]" % (self.name, index)
        raise VolumeNotFound(drive_name=self.name, vol_id=vol_id)

    def volume_id(self, vol_path):
        """
        Retrieves volume id from drive's volume chain using its path.

        libvirt path and Drive.path may be different symlinks
        to the same file or block device:

        - /run/vdsm/storage/sd_id/img_id/vol_id
        - /rhev/data-center/pool_id/sd_id/images/img_id/vol_id
        """
        for vol in self.volumeChain:
            if self.diskType == DISK_TYPE.NETWORK:
                    if vol['path'] == vol_path:
                        return vol['volumeID']
            else:
                if os.path.realpath(vol['path']) == os.path.realpath(vol_path):
                    return vol['volumeID']
        raise LookupError("Unable to find VolumeID for path '%s'", vol_path)

    def parse_volume_chain(self, disk_xml):
        """
        Parses libvirt xml and extracts volume chain from it.

        Arguments:
             disk_xml (ElementTree): libvirt xml to parse

        Returns:
            list: VolumeChainEntry[] - List of chain entries where
            each entry contains volume UUID, volume path
            and volume index. For the 'top' volume index
            is None, as 'top' volume have no indices at
            all.

            VolumeChainEntry is reversed in relation to
            libvirt xml: xml is ordered from top to base
            volume, while volume chain is ordered from
            base to the top.

        Raises:
            InvalidBackingStoreIndex exception when index value is not int.
        """
        volChain = []
        index = None
        source_attr = SOURCE_ATTR[self.diskType]
        while True:
            path = vmxml.find_attr(disk_xml, 'source', source_attr)
            if not path:
                break

            if index is not None:
                try:
                    index = int(index)
                except ValueError:
                    raise InvalidBackingStoreIndex(path, index)

            # TODO: Allocation information is not available in the XML.  Switch
            # to the new interface once it becomes available in libvirt.
            alloc = None
            backingstore = next(vmxml.children(disk_xml, 'backingStore'), None)
            if backingstore is None:
                self.log.warning("<backingStore/> missing from backing "
                                 "chain for drive %s", self.name)
                break

            entry = VolumeChainEntry(self.volume_id(path), path, alloc, index)
            volChain.insert(0, entry)

            disk_xml = backingstore
            index = vmxml.attr(backingstore, 'index')
        return volChain or None

    def get_snapshot_xml(self, snap_info):
        """Libvirt snapshot XML"""
        if 'diskType' in snap_info:
            if self.diskType != snap_info['diskType']:
                raise exception.UnsupportedOperation(
                    "Unexpected diskType",
                    drive_disk_type=self.diskType,
                    snapshot_disk_type=snap_info["diskType"])

        if self.diskType == DISK_TYPE.NETWORK:
            if self.protocol != snap_info['protocol']:
                raise exception.UnsupportedOperation(
                    "Unexpected protocol",
                    drive_protocol=self.protocol,
                    snapshot_protocol=snap_info["protocol"])

        disk = vmxml.Element('disk', name=self.name, snapshot='external',
                             type=self.diskType)

        drive_info = snap_info.copy()
        drive_info["diskType"] = self.diskType
        snap_elem = _getSourceXML(drive_info)

        # Type attribute is needed but not documented:
        # https://bugzilla.redhat.com/1452103
        snap_elem.setAttrs(type=self.diskType)

        disk.appendChild(snap_elem)
        return disk


def chain_index(actual_chain, vol_id, drive_name):
    """
    Retrieves volume index from the volume chain.

    Arguments:
        actual_chain (VolumeChainEntry[]): Current volume chain
        vol_id (str): Volume's UUID
        drive_name (str): Drive's name

    Returns:
        str: Volume index

    Raises:
        VolumeNotFound exception when volume is not in chain.
    """
    for entry in actual_chain:
        if entry.uuid == vol_id:
            return entry.index
    raise VolumeNotFound(drive_name=drive_name, vol_id=vol_id)


def _getSourceXML(drive):
    """
    Makes a libvirt <source> element for specified drive.

    Arguments:
        drive (dict like): Drive description, A dict or an object
        implementing __getitem__.

    Returns:
        Element: libvirt source element in a form of
                 <source file='/image'/>
    """
    source = vmxml.Element('source')
    if drive["diskType"] == DISK_TYPE.BLOCK:
        source.setAttrs(dev=drive["path"])
    elif drive["diskType"] == DISK_TYPE.NETWORK:
        source.setAttrs(protocol=drive["protocol"], name=drive["path"])
        for host in drive["hosts"]:
            source.appendChildWithArgs('host', **host)
    elif drive["diskType"] == DISK_TYPE.FILE:
        source.setAttrs(file=drive["path"])
        if drive["device"] == 'cdrom' or drive["device"] == 'floppy':
            source.setAttrs(startupPolicy='optional')
    else:
        raise RuntimeError("Unsupported diskType %r", drive["diskType"])
    return source


def _getDriverXML(drive):
    driver = vmxml.Element('driver')
    driverAttrs = {'name': 'qemu'}

    if drive['diskType'] == DISK_TYPE.BLOCK:
        driverAttrs['io'] = 'native'
    else:
        driverAttrs['io'] = 'threads'

    if drive['format'] == 'cow':
        driverAttrs['type'] = 'qcow2'
    elif drive['format']:
        driverAttrs['type'] = 'raw'

    if 'discard' in drive and drive['discard']:
        driverAttrs['discard'] = 'unmap'

    try:
        driverAttrs['iothread'] = str(drive['specParams']['pinToIoThread'])
    except KeyError:
        pass

    driverAttrs['cache'] = drive['cache']

    if (drive['propagateErrors'] == 'on' or
            conv.tobool(drive['propagateErrors'])):
        driverAttrs['error_policy'] = 'enospace'
    else:
        driverAttrs['error_policy'] = 'stop'

    driver.setAttrs(**driverAttrs)
    return driver


def _get_drive_identification(dom):
    source = vmxml.find_first(dom, 'source', None)
    if source is not None:
        devPath = (vmxml.attr(source, 'file') or
                   vmxml.attr(source, 'dev') or
                   vmxml.attr(source, 'name'))
    else:
        devPath = ''
    name = vmxml.find_attr(dom, 'target', 'dev')
    alias = core.find_device_alias(dom)
    return alias, devPath, name


_DEVNAMES = {
    'ide': 'hd',
    'scsi': 'sd',
    'virtio': 'vd',
    'fdc': 'fd',
    'sata': 'sd',
}


_DEVIFACES = {
    'hd': 'ide',
    'sd': 'scsi',  # SATA will be alias for SCSI
    'vd': 'virtio',
    'fd': 'fdc',
}


def makeName(interface, index):
    devindex = base26.encode(index)
    return _DEVNAMES.get(interface, 'hd') + (devindex or 'a')


def splitName(devname):
    prefix = devname[:2]
    if prefix not in _DEVIFACES:
        raise ValueError('Unrecognized device name: %s', devname)
    return _DEVIFACES[prefix], base26.decode(devname[2:])
