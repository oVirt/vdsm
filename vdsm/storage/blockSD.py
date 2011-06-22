#
# Copyright 2009 Red Hat, Inc. and/or its affiliates.
#
# Licensed to you under the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.  See the files README and
# LICENSE_GPL_v2 which accompany this distribution.
#

import os
import threading
import logging
import signal
import errno
import re
from StringIO import StringIO
import time
from operator import itemgetter

from config import config
import constants
import misc
import fileUtils
import sd
import lvm
import safelease
import blockVolume
import multipath
import resourceFactories
from resourceFactories import LVM_ACTIVATION_NAMESPACE
from persistentDict import PersistentDict, DictValidator
import iscsi
import storage_exception as se
from storage_mailbox import MAILBOX_SIZE
import resourceManager as rm

STORAGE_DOMAIN_TAG = "RHAT_storage_domain"
STORAGE_UNREADY_DOMAIN_TAG = STORAGE_DOMAIN_TAG + "_UNREADY"

MASTERLV = "master"
SPECIAL_LVS = (sd.METADATA, sd.LEASES, sd.IDS, sd.INBOX, sd.OUTBOX, MASTERLV)

MASTERLV_SIZE = "1024" #In MiB = 2 ** 20 = 1024 ** 2 => 1GiB

log = logging.getLogger("Storage.BlockSD")

# FIXME: Make this calcuated from something logical
RESERVED_METADATA_SIZE = 40 * (2 ** 20)
RESERVED_MAILBOX_SIZE = MAILBOX_SIZE * safelease.MAX_HOST_ID
METADATA_BASE_SIZE = 378

MAX_PVS_LIMIT = 10 # BZ#648051
MAX_PVS = config.getint('irs', 'maximum_allowed_pvs')
if MAX_PVS > MAX_PVS_LIMIT:
   log.warning("maximum_allowed_pvs = %d ignored. MAX_PVS = %d", MAX_PVS, MAX_PVS_LIMIT)
   MAX_PVS = MAX_PVS_LIMIT

PVS_METADATA_SIZE = MAX_PVS * 142

SD_METADATA_SIZE = 2048

DMDK_VGUUID = "VGUUID"
DMDK_PV_REGEX = re.compile(r"^PV\d+$")

VERS_METADATA_LV = (0,)
VERS_METADATA_TAG = (2,)

def encodePVInfo(pvInfo):
    return (
        "pv:%s," % pvInfo["guid"] +
        "uuid:%s," % pvInfo["uuid"] +
        "pestart:%s," % pvInfo["pestart"] +
        "pecount:%s," % pvInfo["pecount"] +
        "mapoffset:%s" % pvInfo["mapoffset"])


def decodePVInfo(value):
    pvInfo = dict([item.split(":") for item in value.split(",")])
    pvInfo["guid"] = pvInfo["pv"]
    del pvInfo["pv"]
    return pvInfo

BLOCK_SD_MD_FIELDS = sd.SD_MD_FIELDS.copy()
# TBD: Do we really need this key?
BLOCK_SD_MD_FIELDS.update({DMDK_PV_REGEX : (decodePVInfo, encodePVInfo),
                           DMDK_VGUUID : (str, str)})

INVALID_CHARS = re.compile(r"[^a-zA-Z0-9_+.\-/=!:#]")
LVM_ENC_ESCAPE = re.compile("&(\d+)&")

# Move to lvm
def lvmTagEncode(s):
    s = unicode(s)
    return str(INVALID_CHARS.sub(lambda c: "&%s&" % ord(c.group()), s))

def lvmTagDecode(s):
    s = unicode(s)
    return LVM_ENC_ESCAPE.sub(lambda c: unichr(int(c.groups()[0])), s)

class VGTagMetadataRW(object):
    log = logging.getLogger("storage.Metadata.VGTagMetadataRW")
    METADATA_TAG_PREFIX = "MDT_"
    METADATA_TAG_PREFIX_LEN = len(METADATA_TAG_PREFIX)
    def __init__(self, vgName):
        self._vgName = vgName

    def readlines(self):
        lvm.refreshVG(self._vgName)
        vg = lvm.getVG(self._vgName)
        metadata = []
        for tag in vg.tags:
            if not tag.startswith(self.METADATA_TAG_PREFIX):
                continue

            metadata.append(lvmTagDecode(tag[self.METADATA_TAG_PREFIX_LEN:]))

        return metadata

    def writelines(self, lines):
        currentMetadata = set(self.readlines())
        newMetadata = set(lines)

        # Remove all items that do not exist in the new metadata
        toRemove = [self.METADATA_TAG_PREFIX + lvmTagEncode(item) for item in currentMetadata.difference(newMetadata)]

        # Add all missing items that do no exist in the old metadata
        toAdd = [self.METADATA_TAG_PREFIX + lvmTagEncode(item) for item in newMetadata.difference(currentMetadata)]

        if len(toAdd) == 0 and len(toRemove) == 0:
            return

        self.log.debug("Updating metadata adding=%s removing=%s", ", ".join(toAdd), ", ".join(toRemove))
        lvm.changeVGTags(self._vgName, delTags=toRemove, addTags=toAdd)

class LvMetadataRW(object):
    """
    Block Storage Domain metadata implementation
    """
    log = logging.getLogger("storage.Metadata.LvMetadataRW")
    def __init__(self, vgName, lvName, offset, size):
        self._size = size
        self._lvName = lvName
        self._vgName = vgName
        self._offset = offset
        self.metavol = lvm.lvPath(vgName, lvName)

    def readlines(self):
        # Fetch the metadata from metadata volume
        lvm.activateLVs(self._vgName, self._lvName)

        m = misc.readblockSUDO(self.metavol, self._offset, self._size)
        # Read from metadata volume will bring a load of zeroes trailing
        # actual metadata. Strip it out.
        metadata = [i for i in m if len(i) > 0 and i[0] != '\x00' and "=" in i]

        return metadata

    def writelines(self, lines):
        lvm.activateLVs(self._vgName, self._lvName)

        # Write `metadata' to metadata volume
        metaStr = StringIO()

        for line in lines:
            metaStr.write(line)
            metaStr.write("\n")

        if metaStr.pos > self._size:
            raise se.MetadataOverflowError()

        # Clear out previous data - it is a volume, not a file
        metaStr.write('\0' * (self._size - metaStr.pos))

        data = metaStr.getvalue()
        with fileUtils.DirectFile(self.metavol, "r+d") as f:
            f.seek(self._offset)
            f.write(data)

LvBasedSDMetadata = lambda vg, lv : DictValidator(PersistentDict(LvMetadataRW(vg, lv, 0, SD_METADATA_SIZE)), BLOCK_SD_MD_FIELDS)
TagBasedSDMetadata = lambda vg : DictValidator(PersistentDict(VGTagMetadataRW(vg)), BLOCK_SD_MD_FIELDS)

def selectMetadata(sdUUID):
    mdProvider = LvBasedSDMetadata(sdUUID, sd.METADATA)
    if len(mdProvider) > 0:
        metadata = mdProvider
    else:
        metadata = TagBasedSDMetadata(sdUUID)
    return metadata


class BlockStorageDomain(sd.StorageDomain):
    mountpoint = os.path.join(sd.StorageDomain.storage_repository,
            sd.DOMAIN_MNT_POINT, sd.BLOCKSD_DIR)

    def __init__(self, sdUUID):
        domaindir = os.path.join(self.mountpoint, sdUUID)
        metadata = selectMetadata(sdUUID)
        sd.StorageDomain.__init__(self, sdUUID, domaindir, metadata)
        self.refreshSpecialVolumes()
        self.metavol = lvm.lvPath(self.sdUUID, sd.METADATA)

        # _extendlock is used to prevent race between
        # VG extend and LV extend.
        self._extendlock = threading.Lock()
        self.imageGarbageCollector()
        self._registerResourceNamespaces()
        self._lastUncachedSelftest = 0

    def _registerResourceNamespaces(self):
        """
        Register resources namespaces and create
        factories for it.
        """
        sd.StorageDomain._registerResourceNamespaces(self)

        rmanager = rm.ResourceManager.getInstance()
        # Register lvm activation resource namespace for the underlying VG
        lvmActivationFactory = resourceFactories.LvmActivationFactory(self.sdUUID)
        lvmActivationNamespace = sd.getNamespace(self.sdUUID, LVM_ACTIVATION_NAMESPACE)
        try:
            rmanager.registerNamespace(lvmActivationNamespace, lvmActivationFactory)
        except Exception:
            self.log.warn("Resource namespace %s already registered", lvmActivationNamespace)


    @classmethod
    def metaSize(cls, vgroup):
        ''' Calc the minimal meta volume size in MB'''
        # In any case the metadata volume cannot be less than 512MB for the
        # case of 512 bytes per volume metadata, 2K for domain metadata and
        # extent size of 128MB. In any case we compute the right size on line.
        vg = lvm.getVG(vgroup)
        minmetasize = (SD_METADATA_SIZE / sd.METASIZE * int(vg.extent_size) +
            (1024 * 1024 - 1)) / (1024 * 1024)
        metaratio = int(vg.extent_size) / sd.METASIZE
        metasize = (int(vg.extent_count) * sd.METASIZE + (1024*1024-1)) / (1024*1024)
        metasize = max(minmetasize, metasize)
        if metasize > int(vg.free) / (1024*1024):
            raise se.VolumeGroupSizeError("volume group has not enough extents %s (Minimum %s), VG may be too small" % (vg.extent_count, (1024*1024)/sd.METASIZE))
        cls.log.info("size %s MB (metaratio %s)" % (metasize, metaratio))
        return metasize

    @classmethod
    def create(cls, sdUUID, domainName, domClass, vgUUID, storageType, version):
        """ Create new storage domain
            'sdUUID' - Storage Domain UUID
            'domainName' - storage domain name
            'vgUUID' - volume group UUID
            'domClass' - Data/Iso
        """
        cls.log.info("sdUUID=%s domainName=%s domClass=%s vgUUID=%s "
            "storageType=%s version=%s", sdUUID, domainName, domClass, vgUUID,
            storageType, version)

        if len(domainName) > sd.MAX_DOMAIN_DESCRIPTION_SIZE:
            raise se.StorageDomainDescriptionTooLongError()

        sd.validateDomainVersion(version)

        vg = lvm.getVGbyUUID(vgUUID)
        vgName = vg.name

        if set((STORAGE_UNREADY_DOMAIN_TAG,)) != set(vg.tags):
            raise se.VolumeGroupHasDomainTag(vgUUID)
        try:
            lvm.getLV(vgName)
            raise se.StorageDomainNotEmpty(vgUUID)
        except se.LogicalVolumeDoesNotExistError:
            pass

        numOfPVs = len(lvm.listPVNames(vgName))
        if version in VERS_METADATA_LV and numOfPVs > MAX_PVS:
            cls.log.debug("%d > %d" , numOfPVs, MAX_PVS)
            raise se.StorageDomainIsMadeFromTooManyPVs()

        # Set the name of the VG to be the same as sdUUID
        if vgName != sdUUID:
            lvm.renameVG(vgName, sdUUID)
            vgName = sdUUID
        # Create metadata service volume
        metasize = cls.metaSize(vgName)
        lvm.createLV(vgName, sd.METADATA, "%s" % (metasize))
        # Create the mapping right now so the index 0 is guaranteed
        # to belong to the metadata volume. Since the metadata is at
        # least SDMETADATA/METASIZE units, we know we can use the first
        # SDMETADATA bytes of the metadata volume for the SD metadata.
        # pass metadata's dev to ensure it is the first mapping
        mapping = cls.getMetaDataMapping(vgName)

        # Create the rest of the BlockSD internal volumes
        lvm.createLV(vgName, sd.LEASES, sd.LEASES_SIZE)
        lvm.createLV(vgName, sd.IDS, sd.IDS_SIZE)
        lvm.createLV(vgName, sd.INBOX, sd.INBOX_SIZE)
        lvm.createLV(vgName, sd.OUTBOX, sd.OUTBOX_SIZE)
        lvm.createLV(vgName, MASTERLV, MASTERLV_SIZE)

        # Create VMS file system
        _createVMSfs(os.path.join("/dev", vgName, MASTERLV))

        lvm.deactivateLVs(vgName, MASTERLV)

        path = lvm.lvPath(vgName, sd.METADATA)

        # Zero out the metadata and special volumes before use
        try:
            misc.ddCopy("/dev/zero", path, RESERVED_METADATA_SIZE)
            path = lvm.lvPath(vgName, sd.INBOX)
            misc.ddCopy("/dev/zero", path, RESERVED_MAILBOX_SIZE)
            path = lvm.lvPath(vgName, sd.OUTBOX)
            misc.ddCopy("/dev/zero", path, RESERVED_MAILBOX_SIZE)
        except se.ActionStopped, e:
            raise e
        except se.StorageException:
            raise se.VolumesZeroingError(path)

        if version in VERS_METADATA_LV:
            md = LvBasedSDMetadata(vgName, sd.METADATA)
        elif version in VERS_METADATA_TAG:
            md = TagBasedSDMetadata(vgName)

        # create domain metadata
        # FIXME : This is 99% like the metadata in file SD
        #         Do we really need to keep the VGUUID?
        #         no one reads it from here anyway
        initialMetadata = {
                sd.DMDK_VERSION : version,
                sd.DMDK_SDUUID : sdUUID,
                sd.DMDK_TYPE : storageType,
                sd.DMDK_CLASS : domClass,
                sd.DMDK_DESCRIPTION : domainName,
                sd.DMDK_ROLE : sd.REGULAR_DOMAIN,
                sd.DMDK_POOLS : [],
                sd.DMDK_LOCK_POLICY : '',
                sd.DMDK_LOCK_RENEWAL_INTERVAL_SEC : sd.DEFAULT_LEASE_PARAMS[sd.DMDK_LOCK_RENEWAL_INTERVAL_SEC],
                sd.DMDK_LEASE_TIME_SEC : sd.DEFAULT_LEASE_PARAMS[sd.DMDK_LOCK_RENEWAL_INTERVAL_SEC],
                sd.DMDK_IO_OP_TIMEOUT_SEC : sd.DEFAULT_LEASE_PARAMS[sd.DMDK_IO_OP_TIMEOUT_SEC],
                sd.DMDK_LEASE_RETRIES : sd.DEFAULT_LEASE_PARAMS[sd.DMDK_LEASE_RETRIES],
                DMDK_VGUUID : vgUUID
                }

        initialMetadata.update(mapping)

        md.update(initialMetadata)

        # Mark VG with Storage Domain Tag
        try:
            lvm.replaceVGTag(vgName, STORAGE_UNREADY_DOMAIN_TAG, STORAGE_DOMAIN_TAG)
        except se.StorageException:
            raise se.VolumeGroupUninitialized(vgName)

        bsd = BlockStorageDomain(sdUUID)

        bsd.initSPMlease()

        return bsd


    def produceVolume(self, imgUUID, volUUID):
        """
        Produce a type specific volume object
        """
        repoPath = self._getRepoPath()
        return blockVolume.BlockVolume(repoPath, self.sdUUID, imgUUID, volUUID)


    def getVolumeClass(self):
        """
        Return a type specific volume generator object
        """
        return blockVolume.BlockVolume


    @classmethod
    def validateCreateVolumeParams(cls, volFormat, preallocate, srcVolUUID):
        """
        Validate create volume parameters.
        'srcVolUUID' - backing volume UUID
        'volFormat' - volume format RAW/QCOW2
        'preallocate' - sparse/preallocate
        """
        blockVolume.BlockVolume.validateCreateVolumeParams(volFormat, preallocate, srcVolUUID)


    def createVolume(self, imgUUID, size, volFormat, preallocate, diskType, volUUID, desc, srcImgUUID, srcVolUUID):
        """
        Create a new volume
        """
        repoPath = self._getRepoPath()
        return blockVolume.BlockVolume.create(repoPath, self.sdUUID,
                            imgUUID, size, volFormat, preallocate, diskType,
                            volUUID, desc, srcImgUUID, srcVolUUID)

    @classmethod
    def getMetaDataMapping(cls, vgName, oldMapping={}):
        firstDev, firstExtent = lvm.getFirstExt(vgName, sd.METADATA)
        firstExtent = int(firstExtent)
        if firstExtent != 0:
            cls.log.error("INTERNAL: metadata ext is not 0")
            raise se.MetaDataMappingError("vg %s: metadata extent is not the first extent" % vgName)

        pvlist = lvm.listPVNames(vgName)

        pvlist.remove(firstDev)
        pvlist.insert(0, firstDev)
        cls.log.info("Create: SORT MAPPING: %s" % pvlist)

        mapping = {}
        devNum = len(oldMapping)
        for dev in pvlist:
            knownDev = False
            for pvID, oldInfo in oldMapping.iteritems():
                if os.path.basename(dev) == oldInfo["guid"]:
                    mapping[pvID] = oldInfo
                    knownDev = True
                    break

            if knownDev:
                continue

            pv = lvm.getPV(dev)
            pvInfo = {}
            pvInfo["guid"] = os.path.basename(pv.name)
            pvInfo["uuid"] = pv.uuid
            # this is another trick, it's not the
            # the pestart value you expect, it's just
            # 0, always
            pvInfo["pestart"] = 0
            pvInfo["pecount"] = pv.pe_count
            if devNum == 0:
                mapOffset = 0
            else:
                prevDevNum = devNum - 1
                try:
                    prevInfo = mapping["PV%d" % (prevDevNum,)]
                except KeyError:
                    prevInfo = oldMapping["PV%d" % (prevDevNum,)]

                mapOffset = int(prevInfo["mapoffset"]) + int(prevInfo["pecount"])

            pvInfo["mapoffset"] = mapOffset
            mapping["PV%d" % devNum] = pvInfo
            devNum += 1

        return mapping


    def updateMapping(self):
        # First read existing mapping from metadata
        with self._metadata.transaction():
            mapping = self.getMetaDataMapping(self.sdUUID, self.readMetadataMapping())
            for key in set(self._metadata.keys() + mapping.keys()):
                if DMDK_PV_REGEX.match(key):
                    if key in mapping:
                        self._metadata[key] = mapping[key]
                    else:
                        del self._metadata[key]

    def extend(self, devlist):
        mapping = self.readMetadataMapping().values()
        if self.getVersion() in VERS_METADATA_LV:
            if len(mapping) + len(devlist) > MAX_PVS:
                raise se.StorageDomainIsMadeFromTooManyPVs()

        self._extendlock.acquire()
        try:

            knowndevs = list(multipath.getMPDevNamesIter())
            devices = []

            for dev in devlist:
                if dev in knowndevs:
                    devices.append(dev)
                else:
                    raise se.InvalidPhysDev(dev)

            lvm.extendVG(self.sdUUID, devices)
            self.updateMapping()
            newsize = self.metaSize(self.sdUUID)
            lvm.extendLV(self.sdUUID, sd.METADATA, newsize)

        finally:
            self._extendlock.release()

    def mapMetaOffset(self, vol_name):
        if self.getVersion() in VERS_METADATA_LV:
            return self.getVolumeMetadataOffsetFromPvMapping(vol_name)
        else:
            return self.getFreeMetadataSlot(blockVolume.VOLUME_METASIZE)

    def _getOccupiedMetadataSlots(self):
        stripPrefix = lambda s, pfx : s[len(pfx):]
        occupiedSlots = []
        for lv in lvm.getLV(self.sdUUID):
            if lv.name in SPECIAL_LVS:
                # Special LVs have no mapping
                continue

            offset = None
            size = None
            for tag in lv.tags:
                if tag.startswith(blockVolume.TAG_PREFIX_MD):
                    offset = int(stripPrefix(tag, blockVolume.TAG_PREFIX_MD))

                if offset is not None and size is not None:
                    # I've found everything I need
                    break

            if offset is None:
                self.log.warn("Could not find mapping for lv %s/%s", self.sdUUID, lv.name)
                continue

            if size is None:
                size = blockVolume.VOLUME_METASIZE

            occupiedSlots.append((offset, size))

        occupiedSlots.sort(key=itemgetter(0))
        return occupiedSlots

    def getFreeMetadataSlot(self, slotSize):
        occupiedSlots = self._getOccupiedMetadataSlots()

        # It might look weird skipping the sd metadata
        # when it has been moved to tags. But this is
        # here because domain metadata and volume
        # metadata look the same. The domain might get
        # confused and think it has lv metadata if it
        # finds something is written in that area.
        freeSlot = SD_METADATA_SIZE
        for offset, size in occupiedSlots:
            if offset - freeSlot > slotSize:
                break

            freeSlot = offset + size

        self.log.debug("Found freeSlot %s in VG %s", freeSlot, self.sdUUID)
        return freeSlot

    def getVolumeMetadataOffsetFromPvMapping(self, vol_name):
        dev, ext = lvm.getFirstExt(self.sdUUID, vol_name)
        self.log.debug("vol %s dev %s ext %s" % (vol_name, dev, ext))
        for pv in self.readMetadataMapping().values():
            self.log.debug("MAPOFFSET: pv %s -- dev %s ext %s" % (pv, dev, ext))
            pestart = int(pv["pestart"])
            pecount = int(pv["pecount"])
            if (os.path.basename(dev) == pv["guid"] and
                int(ext) in range(pestart, pestart + pecount)):

                offs =  int(ext) + int(pv["mapoffset"])
                if offs < SD_METADATA_SIZE/sd.METASIZE:
                    raise se.MetaDataMappingError("domain %s: vol %s MD offset %s is bad - will overwrite SD's MD" % (self.sdUUID, vol_name, offs))
                return offs
        raise se.MetaDataMappingError("domain %s: can't map PV %s ext %s" % (self.sdUUID, dev, ext))

    def readMetadataMapping(self):
        meta = self.getMetadata()
        for key in meta.keys():
            if not DMDK_PV_REGEX.match(key):
                del meta[key]

        self.log.info("META MAPPING: %s" % meta)
        return meta

    def _getLeasesFilePath(self):
        lvm.activateLVs(self.sdUUID, [sd.LEASES])
        return lvm.lvPath(self.sdUUID, sd.LEASES)

    def upgrade(self, targetVersion):
        sd.validateDomainVersion(targetVersion)
        self.invalidateMetadata()
        version = self.getVersion()
        self.log.debug("Trying to upgrade domain `%s` from version %d to version %d", self.sdUUID, version, targetVersion)
        if version > targetVersion:
            raise se.CurrentVersionTooAdvancedError(self.sdUUID,
                    curVer=version, expVer=targetVersion)

        elif version == targetVersion:
            self.log.debug("No need to upgrade domain `%s`, leaving unchanged", self.sdUUID)
            return

        self.log.debug("Upgrading domain `%s`", self.sdUUID)
        if targetVersion in VERS_METADATA_LV:
            self.setMetaParam(sd.DMDK_VERSION, targetVersion)

        if targetVersion in VERS_METADATA_TAG:
            self.log.debug("Upgrading domain `%s` to tag based metadata", self.sdUUID)
            newProvider = TagBasedSDMetadata(self.sdUUID)
            oldProvider = self._metadata
            # I use _dict to bypass the validators
            # We need to copy ALL metadata
            metadata = oldProvider._dict.copy()
            metadata[sd.DMDK_VERSION] = str(targetVersion)
            newProvider._dict.update(metadata)
            try:
                self._metadata = newProvider
                oldProvider._dict.clear()
            except:
                self.log.error("Could not commit upgrade", exc_info=True)
                newProvider._dict.clear()
                self._metadata = oldProvider
                raise



    def selftest(self):
        """
        Run the underlying VG validation routine
        """
        useCache = True
        timeout = config.getint("irs", "repo_stats_cache_refresh_timeout")
        now = time.time()
        if now - self._lastUncachedSelftest > timeout:
            useCache = False
            self._lastUncachedSelftest = now

        return self.validate(useCache=useCache)

    def validate(self, useCache=False):
        """
        Validate that the storage domain metadata
        """
        self.log.info("sdUUID=%s", self.sdUUID)
        if not useCache:
            lvm.chkVG(self.sdUUID)
        elif lvm.getVG(self.sdUUID).partial != lvm.VG_OK:
            raise se.StorageDomainAccessError(self.sdUUID)

        if not useCache:
            self.invalidateMetadata()
        self.getMetadata()
        return True

    def invalidate(self):
        """
        Make sure that storage domain is inaccessible.
        1. Make sure master LV is not mounted
        2. Deactivate all the volumes from the underlying VG
        3. Destroy any possible dangling maps left in device mapper
        """
        try:
            self.unmountMaster()
        except se.StorageDomainMasterUnmountError:
            self.log.warning("Unable to unmount master LV during invalidateSD")
        except se.CannotDeactivateLogicalVolume:
            # It could be that at this point there is no LV, so just ignore it
            pass
        except Exception:
            # log any other exception, but keep going
            self.log.error("Unexpected error", exc_info=True)

        # FIXME: remove this and make sure nothing breaks
        try:
            lvm.deactivateVG(self.sdUUID)
        except Exception:
            # log any other exception, but keep going
            self.log.error("Unexpected error", exc_info=True)

        fileUtils.cleanupdir(os.path.join("/dev", self.sdUUID))

    @classmethod
    def format(cls, sdUUID, domaindir):
        """Format detached storage domain.
           This removes all data from the storage domain.
        """
        # Remove the directory tree
        fileUtils.cleanupdir(domaindir, ignoreErrors = True)
        # Remove special metadata and service volumes
        # Remove all volumes LV if exists
        _removeVMSfs(lvm.lvPath(sdUUID, MASTERLV))
        try:
            lvs = lvm.getLV(sdUUID)
        except se.LogicalVolumeDoesNotExistError:
            lvs = () #No LVs in this VG (domain)

        for lv in lvs:
            #Fix me: Should raise and get resource lock.
            try:
                lvm.removeLV(sdUUID, lv.name)
            except se.CannotRemoveLogicalVolume, e:
                cls.log.warning("Remove logical volume failed %s/%s %s", sdUUID, lv.name, str(e))

        # Remove SD tag
        lvm.replaceVGTag(sdUUID, STORAGE_DOMAIN_TAG, STORAGE_UNREADY_DOMAIN_TAG)
        return True

    def getInfo(self):
        """
        Get storage domain info
        """
        ##self.log.info("sdUUID=%s", self.sdUUID)
        # First call parent getInfo() - it fills in all the common details
        info = sd.StorageDomain.getInfo(self)
        # Now add blockSD specific data
        vg = lvm.getVG(self.sdUUID) #vg.name = self.sdUUID
        info['vguuid'] = vg.uuid
        info['state'] = vg.partial
        return info

    def getStats(self):
        """
        """
        vg = lvm.getVG(self.sdUUID)
        return dict(disktotal=vg.size, diskfree=vg.free)

    def getAllImages(self):
        """
        Get list of all images
        """
        try:
            lvs = lvm.getLV(self.sdUUID)
        except se.LogicalVolumeDoesNotExistError:
            lvs = () #No LVs in this VG (domain)

        # Collect all the tags from all the volumes, but ignore duplicates
        # set conveniently does exactly that
        tags = set()
        for lv in lvs:
            tags.update(lv.tags)
        # Drop non image tags and strip prefix
        taglen = len(blockVolume.TAG_PREFIX_IMAGE)
        images = [ i[taglen:] for i in tags
                        if i.startswith(blockVolume.TAG_PREFIX_IMAGE) ]
        return images

    def validateMasterMount(self):
        return fileUtils.isMounted(mountPoint=self.getMasterDir())

    def mountMaster(self):
        """
        Mount the master metadata file system. Should be called only by SPM.
        """
        lvm.activateLVs(self.sdUUID, MASTERLV)
        masterDir = os.path.join(self.domaindir, sd.MASTER_FS_DIR)
        fileUtils.createdir(masterDir)

        masterfsdev = lvm.lvPath(self.sdUUID, MASTERLV)
        cmd = [constants.EXT_FSCK, "-p", masterfsdev]
        (rc, out, err) = misc.execCmd(cmd)
        # fsck exit codes
        # 0    - No errors
        # 1    - File system errors corrected
        # 2    - File system errors corrected, system should
        #        be rebooted
        # 4    - File system errors left uncorrected
        # 8    - Operational error
        # 16   - Usage or syntax error
        # 32   - E2fsck canceled by user request
        # 128  - Shared library error
        if rc == 1 or rc == 2:
           # rc is a number
           self.log.info("fsck corrected fs errors (%s)", rc)
        if rc >= 4:
            raise se.BlockStorageDomainMasterFSCKError(masterfsdev, rc)

        # TODO: Remove when upgrade is only from a version which creates ext3
        # Try to add a journal - due to unfortunate circumstances we exposed
        # to the public the code that created ext2 file system instead of ext3.
        # In order to make up for it we are trying to add journal here, just
        # to be sure (and we have fixed the file system creation).
        # If there is a journal already tune2fs will do nothing, indicating this
        # condition only with exit code. However, we do not really care.
        cmd = [constants.EXT_TUNE2FS, "-j", masterfsdev]
        misc.execCmd(cmd)

        rc = fileUtils.mount(masterfsdev, masterDir, mountType=fileUtils.FSTYPE_EXT3)
        # mount exit codes
        # mount has the following return codes (the bits can be ORed):
        # 0      success
        # 1      incorrect invocation or permissions
        # 2      system error (out of memory, cannot fork, no more loop devices)
        # 4      internal mount bug or missing nfs support in mount
        # 8      user interrupt
        # 16     problems writing or locking /etc/mtab
        # 32     mount failure
        # 64     some mount succeeded
        if rc != 0:
            raise se.BlockStorageDomainMasterMountError(masterfsdev, rc, out)

        cmd = [constants.EXT_CHOWN, lvm.USER_GROUP, masterDir]
        (rc, out, err) = misc.execCmd(cmd)
        if rc != 0:
            self.log.error("failed to chown %s", masterDir)

    @classmethod
    def __handleStuckUmount(cls, masterDir):
        umountPids = misc.pgrep("umount")
        for umountPid in umountPids:
            try:
                state = misc.pidStat(umountPid)[2]
                mountPoint = misc.getCmdArgs(umountPid)[-1]
            except:
                # Process probably exited
                continue

            if mountPoint != masterDir:
                continue

            if state != "D":
                # If the umount is not in d state there
                # is a possiblity that the world might
                # be in flux and umount will get stuck
                # in an unkillable state that is not D
                # which I don't know about, perhaps a
                # bug in umount will cause umount to
                # wait for something unrelated that is
                # not the syscall. Waiting on a process
                # which is not your child is race prone
                # I will just call for another umount
                # and wait for it to finish. That way I
                # know that a umount ended.
                try:
                    rc = fileUtils.umount(mountPoint=masterDir)
                    if rc == 0:
                        return
                except:
                    # timeout! we are stuck again.
                    # if you are here spmprotect forgot to
                    # reboot the machine but in any case
                    # continue with the disconnection.
                    pass

            try:
                vgName = masterDir.rsplit("/", 2)[1]
                masterDev = os.path.join("/dev/mapper", vgName.replace("-", "--") + "-" + MASTERLV)
            except KeyError:
                # Umount succeeded after all
                return

            cls.log.warn("master mount resource is `%s`, trying to disconnect underlying storage", masterDev)
            iscsi.disconnectFromUndelyingStorage(masterDev)

    @classmethod
    def doUnmountMaster(cls, masterdir):
        """
        Unmount the master metadata file system. Should be called only by SPM.
        """
        # fuser processes holding mount point and validate that the umount succeeded
        cls.__handleStuckUmount(masterdir)
        if fileUtils.isMounted(mountPoint=masterdir):
            # Try umount, take 1
            fileUtils.umount(mountPoint=masterdir)
            if fileUtils.isMounted(mountPoint=masterdir):
                # umount failed, try to kill that processes holding mount point
                fuser_cmd = [constants.EXT_FUSER, "-m", masterdir]
                (rc, out, err) = misc.execCmd(fuser_cmd)

                # It was unmounted while I was checking no need to do anything
                if not fileUtils.isMounted(mountPoint=masterdir):
                    return
                cls.log.warn(out)
                if len(out) == 0:
                    cls.log.warn("Unmount failed because of errors that fuser can't solve")
                else:
                    for match in out[0].split():
                        try:
                            pid = int(match)
                        except ValueError:
                            # Match can be "kernel"
                            continue

                        try:
                            cls.log.debug("Trying to kill pid %d", pid)
                            os.kill(pid, signal.SIGKILL)
                        except OSError, e:
                            if e.errno == errno.ESRCH: # No such process
                                pass
                            elif e.errno == errno.EPERM: # Operation not permitted
                                cls.log.warn("Could not kill pid %d because operation was not permitted", pid)
                            else:
                                cls.log.warn("Could not kill pid %d because an unexpected error", exc_info = True)
                        except:
                            cls.log.warn("Could not kill pid %d because an unexpected error", exc_info = True)

                # Try umount, take 2
                fileUtils.umount(mountPoint=masterdir)
                if fileUtils.isMounted(mountPoint=masterdir):
                    # We failed to umount masterFS
                    # Forcibly rebooting the SPM host would be safer. ???
                    raise se.StorageDomainMasterUnmountError(masterdir, rc)

    def unmountMaster(self):
        """
        Unmount the master metadata file system. Should be called only by SPM.
        """
        masterdir = os.path.join(self.domaindir, sd.MASTER_FS_DIR)
        self.doUnmountMaster(masterdir)
        # It is time to deactivate the master LV now
        lvm.deactivateLVs(self.sdUUID, MASTERLV)


    def refreshDirTree(self):
        # create domain images folder
        imagesPath = os.path.join(self.domaindir, sd.DOMAIN_IMAGES)
        fileUtils.createdir(imagesPath)

        # create domain special volumes folder
        domMD = os.path.join(self.domaindir, sd.DOMAIN_META_DATA)
        fileUtils.createdir(domMD)

        lvm.activateLVs(self.sdUUID, SPECIAL_LVS)
        for lvName in SPECIAL_LVS:
            dst = os.path.join(domMD, lvName)
            if not os.path.lexists(dst):
                src = lvm.lvPath(self.sdUUID, lvName)
                os.symlink(src, dst)

        # create special imageUUID for ISO/Floppy volumes
        isoPath = os.path.join(imagesPath, sd.ISO_IMAGE_UUID)
        if self.isISO():
            fileUtils.createdir(isoPath)

    def refreshSpecialVolumes(self):
        lvm.activateLVs(self.sdUUID, SPECIAL_LVS)

    def extendVolume(self, volumeUUID, size, isShuttingDown=None):
        self._extendlock.acquire()
        try:
            lvm.extendLV(self.sdUUID, volumeUUID, size) #, isShuttingDown) # FIXME
        finally:
            self._extendlock.release()

    def refresh(self):
        self.refreshDirTree()
        lvm.refreshVG(self.sdUUID)
        self._metadata = selectMetadata(self.sdUUID)

    @staticmethod
    def findDomainPath(sdUUID):
        try:
            vg = lvm.getVG(sdUUID)
        except se.VolumeGroupDoesNotExist:
            raise se.StorageDomainDoesNotExist()

        if _isSD(vg):
            return vg.name

        raise se.StorageDomainDoesNotExist()


def _createVMSfs(dev):
    """
    Create a special file system to store VM data
    """
    cmd = [constants.EXT_MKFS, "-q", "-j", "-K", dev]
    rc = misc.execCmd(cmd)[0]
    if rc != 0:
        raise se.MkfsError(dev)

def _removeVMSfs(dev):
    """
    Destroy special VM data file system
    """
    # XXX Add at least minimal sanity check:. i.e. fs not mounted
    pass

def _isSD(vg):
    return STORAGE_DOMAIN_TAG in vg.tags

def findDomain(sdUUID):
    return BlockStorageDomain(BlockStorageDomain.findDomainPath(sdUUID))

def getStorageDomainsList():
    return [vg.name for vg in lvm.getAllVGs() if _isSD(vg)]
