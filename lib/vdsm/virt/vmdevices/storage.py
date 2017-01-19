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
from __future__ import absolute_import

import collections
import os
import xml.etree.ElementTree as ET

from vdsm.common import errors
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


VolumeChainEntry = collections.namedtuple('VolumeChainEntry',
                                          ['uuid', 'path', 'allocation'])


class Drive(core.Base):
    __slots__ = ('iface', '_path', 'readonly', 'bootOrder', 'domainID',
                 'poolID', 'imageID', 'UUID', 'volumeID', 'format',
                 'propagateErrors', 'address', 'apparentsize', 'volumeInfo',
                 'index', 'name', 'optional', 'shared', 'truesize',
                 'volumeChain', 'baseVolumeID', 'serial', 'reqsize', 'cache',
                 '_blockDev', 'extSharedState', 'drv', 'sgio', 'GUID',
                 'diskReplicate', '_diskType', 'hosts', 'protocol', 'auth',
                 'discard', 'vm_custom')
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
        super(Drive, self).__init__(log, **kwargs)
        if not hasattr(self, 'vm_custom'):
            self.vm_custom = {}
        self.device = getattr(self, 'device', 'disk')
        # Keep sizes as int
        self.reqsize = int(kwargs.get('reqsize', '0'))  # Backward compatible
        self.truesize = int(kwargs.get('truesize', '0'))
        self.apparentsize = int(kwargs.get('apparentsize', '0'))
        self.name = makeName(self.iface, self.index)
        self.cache = config.get('vars', 'qemu_drive_cache')
        self.discard = kwargs.get('discard', False)

        self._blockDev = None  # Lazy initialized

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
        return self.blockDev and self.format == "cow"

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
    def networkDev(self):
        return getattr(self, '_diskType', None) == DISK_TYPE.NETWORK

    @property
    def blockDev(self):
        if self._blockDev is None:
            if self.networkDev or self.device in ("cdrom", "floppy"):
                self._blockDev = False
            else:
                try:
                    self._blockDev = utils.isBlockDevice(self.path)
                except Exception:
                    self.log.debug("Unable to determine if the path '%s' is a "
                                   "block device", self.path, exc_info=True)
        return self._blockDev

    @property
    def path(self):
        return self._path

    @path.setter
    def path(self, path):
        if hasattr(self, "_path") and self._path != path:
            self.log.debug("Drive %s moved from %r to %r",
                           self.name, self._path, path)
            # After live storage migration domain type may have changed
            # invalidating cached blockDev.
            self._blockDev = None
        self._path = path

    @property
    def diskType(self):
        if self.blockDev:
            return DISK_TYPE.BLOCK
        elif self.networkDev:
            return DISK_TYPE.NETWORK
        else:
            return DISK_TYPE.FILE

    @diskType.setter
    def diskType(self, value):
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

        if hasattr(self, 'readonly') and utils.tobool(self.readonly):
            diskelem.appendChildWithArgs('readonly')
        elif self.device == 'floppy' and not hasattr(self, 'readonly'):
            # floppies are used only internally for sysprep, so
            # they are readonly unless explicitely stated otherwise
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

    def volume_path(self, vol_id):
        """
        Retrieves volume path from drive's volume chain using its's ID.
        """
        for v in self.volumeChain:
            if v['volumeID'] == vol_id:
                return v['path']
        raise VolumeNotFound(drive_name=self.name, vol_id=vol_id)

    def parse_volume_chain(self, disk_xml):
        def path_to_vol_id(path):
            for vol in self.volumeChain:
                if os.path.realpath(vol['path']) == os.path.realpath(path):
                    return vol['volumeID']
            raise LookupError("Unable to find VolumeID for path '%s'", path)

        volChain = []
        while True:
            sourceAttr = ('file', 'dev')[self.blockDev]
            path = vmxml.find_attr(disk_xml, 'source', sourceAttr)
            if not path:
                break

            # TODO: Allocation information is not available in the XML.  Switch
            # to the new interface once it becomes available in libvirt.
            alloc = None
            backingstore = next(vmxml.children(disk_xml, 'backingStore'), None)
            if backingstore is None:
                self.log.warning("<backingStore/> missing from backing "
                                 "chain for drive %s", self.name)
                break
            disk_xml = backingstore
            entry = VolumeChainEntry(path_to_vol_id(path), path, alloc)
            volChain.insert(0, entry)
        return volChain or None


def _getSourceXML(drive):
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
            utils.tobool(drive['propagateErrors'])):
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
    alias = vmxml.find_attr(dom, 'alias', 'name')
    return alias, devPath, name


def makeName(interface, index):
    devname = {'ide': 'hd', 'scsi': 'sd', 'virtio': 'vd', 'fdc': 'fd',
               'sata': 'sd'}
    devindex = ''

    i = int(index)
    while i > 0:
        devindex = chr(ord('a') + (i % 26)) + devindex
        i /= 26

    return devname.get(interface, 'hd') + (devindex or 'a')
