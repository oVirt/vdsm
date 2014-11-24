#
# Copyright 2008-2014 Red Hat, Inc.
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

from vdsm.config import config
from vdsm import constants
from vdsm import utils

from .. import vmxml

from .core import Base


class DRIVE_SHARED_TYPE:
    NONE = "none"
    EXCLUSIVE = "exclusive"
    SHARED = "shared"
    TRANSIENT = "transient"

    @classmethod
    def getAllValues(cls):
        # TODO: use introspection
        return (cls.NONE, cls.EXCLUSIVE, cls.SHARED, cls.TRANSIENT)


class Drive(Base):
    __slots__ = ('iface', 'path', 'readonly', 'bootOrder', 'domainID',
                 'poolID', 'imageID', 'UUID', 'volumeID', 'format',
                 'propagateErrors', 'address', 'apparentsize', 'volumeInfo',
                 'index', 'name', 'optional', 'shared', 'truesize',
                 'volumeChain', 'baseVolumeID', 'serial', 'reqsize', 'cache',
                 '_blockDev', 'extSharedState', 'drv', 'sgio', 'GUID',
                 'diskReplicate')
    VOLWM_CHUNK_MB = config.getint('irs', 'volume_utilization_chunk_mb')
    VOLWM_FREE_PCT = 100 - config.getint('irs', 'volume_utilization_percent')
    VOLWM_CHUNK_REPLICATE_MULT = 2  # Chunk multiplier during replication

    def __init__(self, conf, log, **kwargs):
        if not kwargs.get('serial'):
            self.serial = kwargs.get('imageID'[-20:]) or ''
        super(Drive, self).__init__(conf, log, **kwargs)
        # Keep sizes as int
        self.reqsize = int(kwargs.get('reqsize', '0'))  # Backward compatible
        self.truesize = int(kwargs.get('truesize', '0'))
        self.apparentsize = int(kwargs.get('apparentsize', '0'))
        self.name = self._makeName()
        self.cache = config.get('vars', 'qemu_drive_cache')

        if self.device in ("cdrom", "floppy"):
            self._blockDev = False
        else:
            self._blockDev = None

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
        Returns the volume extension chunks (used for the thin provisioning
        on block devices). The value is based on the vdsm configuration but
        can also dynamically change according to the VM needs (e.g. increase
        during a live storage migration).
        """
        if self.isDiskReplicationInProgress():
            return self.VOLWM_CHUNK_MB * self.VOLWM_CHUNK_REPLICATE_MULT
        return self.VOLWM_CHUNK_MB

    @property
    def watermarkLimit(self):
        """
        Returns the watermark limit, when the LV usage reaches this limit an
        extension is in order (thin provisioning on block devices).
        """
        return (self.VOLWM_FREE_PCT * self.volExtensionChunk *
                constants.MEGAB / 100)

    def getNextVolumeSize(self, curSize):
        """
        Returns the next volume size in megabytes. This value is based on the
        volExtensionChunk property and it's the size that should be requested
        for the next LV extension.  curSize is the current size of the volume
        to be extended.  For the leaf volume curSize == self.apparentsize.
        For internal volumes it is discovered by calling irs.getVolumeSize().
        """
        nextSize = (self.volExtensionChunk +
                    ((curSize + constants.MEGAB - 1) / constants.MEGAB))
        return min(nextSize, self.truesize)

    @property
    def networkDev(self):
        try:
            return self.volumeInfo['volType'] == "network"
        except AttributeError:
            # To handle legacy and removable drives.
            return False

    @property
    def blockDev(self):
        if self.networkDev:
            return False

        if self._blockDev is None:
            try:
                self._blockDev = utils.isBlockDevice(self.path)
            except Exception:
                self.log.debug("Unable to determine if the path '%s' is a "
                               "block device", self.path, exc_info=True)
        return self._blockDev

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
                self.cache = self.conf['custom']['viodiskcache']
            except KeyError:
                pass  # Ignore if custom disk cache is missing

    def _makeName(self):
        devname = {'ide': 'hd', 'scsi': 'sd', 'virtio': 'vd', 'fdc': 'fd'}
        devindex = ''

        i = int(self.index)
        while i > 0:
            devindex = chr(ord('a') + (i % 26)) + devindex
            i /= 26

        return devname.get(self.iface, 'hd') + (devindex or 'a')

    def _checkIoTuneCategories(self, ioTuneParamsInfo):
        categories = ("bytes", "iops")
        for category in categories:
            if ioTuneParamsInfo.get('total_' + category + '_sec', 0) and \
                    (ioTuneParamsInfo.get('read_' + category + '_sec', 0) or
                     ioTuneParamsInfo.get('write_' + category + '_sec', 0)):
                raise ValueError('A non-zero total value and non-zero'
                                 ' read/write value for %s_sec can not be'
                                 ' set at the same time' % category)

    def _validateIoTuneParams(self, params):
        ioTuneParams = ('total_bytes_sec', 'read_bytes_sec',
                        'write_bytes_sec', 'total_iops_sec',
                        'write_iops_sec', 'read_iops_sec')
        for key, value in params.iteritems():
            try:
                if key in ioTuneParams:
                    params[key] = int(value)
                    if params[key] >= 0:
                        continue
                else:
                    raise Exception('parameter %s name is invalid' % key)
            except ValueError as e:
                e.args = ('an integer is required for ioTune'
                          ' parameter %s' % key,) + e.args[1:]
                raise
            else:
                raise ValueError('parameter %s value should be'
                                 ' equal or greater than zero' % key)

        self._checkIoTuneCategories(params)

    def getLeasesXML(self):
        """
        Create domxml for the drive lease.

        <lease>
            <key>volumeID</key>
            <lockspace>domainID</lockspace>
            <target offset="0" path="/path/to/lease"/>
        </lease>
        """
        if not self.hasVolumeLeases:
            return  # empty items generator

        # NOTE: at the moment we are generating the lease only for the leaf,
        # when libvirt will support shared leases this will loop over all the
        # volumes
        for volInfo in self.volumeChain[-1:]:
            lease = vmxml.Element('lease')
            lease.appendChildWithArgs('key', text=volInfo['volumeID'])
            lease.appendChildWithArgs('lockspace',
                                      text=volInfo['domainID'])
            lease.appendChildWithArgs('target', path=volInfo['leasePath'],
                                      offset=str(volInfo['leaseOffset']))
            yield lease

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
        self.device = getattr(self, 'device', 'disk')

        source = vmxml.Element('source')
        if self.blockDev:
            deviceType = 'block'
            source.setAttrs(dev=self.path)
        elif self.networkDev:
            deviceType = 'network'
            source.setAttrs(protocol=self.volumeInfo['protocol'],
                            name=self.volumeInfo['path'])
            hostAttrs = {'name': self.volumeInfo['volfileServer'],
                         'port': self.volumeInfo['volPort'],
                         'transport': self.volumeInfo['volTransport']}
            source.appendChildWithArgs('host', **hostAttrs)
        else:
            deviceType = 'file'
            sourceAttrs = {'file': self.path}
            if self.device == 'cdrom' or self.device == 'floppy':
                sourceAttrs['startupPolicy'] = 'optional'
            source.setAttrs(**sourceAttrs)
        diskelem = self.createXmlElem('disk', deviceType,
                                      ['device', 'address', 'sgio'])
        diskelem.setAttrs(snapshot='no')
        diskelem.appendChild(source)

        targetAttrs = {'dev': self.name}
        if self.iface:
            targetAttrs['bus'] = self.iface
        diskelem.appendChildWithArgs('target', **targetAttrs)

        if self.extSharedState == DRIVE_SHARED_TYPE.SHARED:
            diskelem.appendChildWithArgs('shareable')
        if hasattr(self, 'readonly') and utils.tobool(self.readonly):
            diskelem.appendChildWithArgs('readonly')
        elif self.device == 'floppy' and not hasattr(self, 'readonly'):
            # floppies are used only internally for sysprep, so
            # they are readonly unless explicitely stated otherwise
            diskelem.appendChildWithArgs('readonly')
        if hasattr(self, 'serial'):
            diskelem.appendChildWithArgs('serial', text=self.serial)
        if hasattr(self, 'bootOrder'):
            diskelem.appendChildWithArgs('boot', order=self.bootOrder)

        if self.device != 'lun' and hasattr(self, 'sgio'):
            raise ValueError("sgio attribute can be set only for LUN devices")

        if self.device == 'lun' and self.format == 'cow':
            raise ValueError("cow format is not supported for LUN devices")

        if self.device == 'disk' or self.device == 'lun':
            driverAttrs = {'name': 'qemu'}
            if self.blockDev:
                driverAttrs['io'] = 'native'
            else:
                driverAttrs['io'] = 'threads'
            if self.format == 'cow':
                driverAttrs['type'] = 'qcow2'
            elif self.format:
                driverAttrs['type'] = 'raw'

            driverAttrs['cache'] = self.cache

            if (self.propagateErrors == 'on' or
                    utils.tobool(self.propagateErrors)):
                driverAttrs['error_policy'] = 'enospace'
            else:
                driverAttrs['error_policy'] = 'stop'
            diskelem.appendChildWithArgs('driver', **driverAttrs)

        if hasattr(self, 'specParams') and 'ioTune' in self.specParams:
            self._validateIoTuneParams(self.specParams['ioTune'])
            iotune = vmxml.Element('iotune')
            for key, value in self.specParams['ioTune'].iteritems():
                iotune.appendChildWithArgs(key, text=str(value))
            diskelem.appendChild(iotune)

        return diskelem
