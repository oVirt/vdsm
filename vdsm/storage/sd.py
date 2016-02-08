#
# Copyright 2009-2011 Red Hat, Inc.
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

import os
import logging
import types
import threading
from collections import namedtuple
import codecs

import image
import storage_exception as se
import misc
import resourceFactories
from resourceFactories import IMAGE_NAMESPACE, VOLUME_NAMESPACE
import resourceManager as rm
from vdsm import constants
import clusterlock
import outOfProcess as oop
from persistentDict import unicodeEncoder, unicodeDecoder
import volume

from vdsm.config import config

DOMAIN_MNT_POINT = 'mnt'
DOMAIN_META_DATA = 'dom_md'
DOMAIN_IMAGES = 'images'
# Domain's metadata volume name
METADATA = "metadata"
# (volume) meta data slot size
METASIZE = 512
# Domain metadata slot size (it always takes the first slot)
MAX_DOMAIN_DESCRIPTION_SIZE = 50

GLUSTERSD_DIR = "glusterSD"

BLOCKSD_DIR = "blockSD"
LEASES = "leases"
IDS = "ids"
INBOX = "inbox"
OUTBOX = "outbox"

SPECIAL_VOLUME_SIZES_MIB = {
    LEASES: 2048,
    IDS: 8,
    INBOX: 16,
    OUTBOX: 16,
}

# Storage Domain Types
UNKNOWN_DOMAIN = 0
NFS_DOMAIN = 1
FCP_DOMAIN = 2
ISCSI_DOMAIN = 3
LOCALFS_DOMAIN = 4
CIFS_DOMAIN = 5
POSIXFS_DOMAIN = 6
GLUSTERFS_DOMAIN = 7

BLOCK_DOMAIN_TYPES = [FCP_DOMAIN, ISCSI_DOMAIN]
FILE_DOMAIN_TYPES = [NFS_DOMAIN, LOCALFS_DOMAIN, CIFS_DOMAIN, POSIXFS_DOMAIN,
                     GLUSTERFS_DOMAIN]

# use only upper case for values - see storageType()
DOMAIN_TYPES = {UNKNOWN_DOMAIN: 'UNKNOWN', NFS_DOMAIN: 'NFS',
                FCP_DOMAIN: 'FCP', ISCSI_DOMAIN: 'ISCSI',
                LOCALFS_DOMAIN: 'LOCALFS', CIFS_DOMAIN: 'CIFS',
                POSIXFS_DOMAIN: 'POSIXFS', GLUSTERFS_DOMAIN: 'GLUSTERFS'}

# Storage Domains Statuses: keep them capitalize
# DOM_UNINITIALIZED_STATUS = 'Uninitialized'
# DOM_DESTROYED_STATUS = 'Destroyed'
DEPRECATED_DOM_INACTIVE_STATUS = 'Inactive'
# DOM_ERROR_STATUS = 'Error'
# FIXME : domain statuses are pool constants
DOM_UNKNOWN_STATUS = 'Unknown'
DOM_ATTACHED_STATUS = 'Attached'
DOM_UNATTACHED_STATUS = 'Unattached'
DOM_ACTIVE_STATUS = 'Active'

DOMAIN_STATUSES = [DOM_UNKNOWN_STATUS, DOM_ATTACHED_STATUS,
                   DOM_UNATTACHED_STATUS, DOM_ACTIVE_STATUS]
DEPRECATED_STATUSES = {DEPRECATED_DOM_INACTIVE_STATUS: DOM_ATTACHED_STATUS}

# Domain Role
MASTER_DOMAIN = 'Master'
REGULAR_DOMAIN = 'Regular'
# Domain Class
DATA_DOMAIN = 1
ISO_DOMAIN = 2
BACKUP_DOMAIN = 3
DOMAIN_CLASSES = {DATA_DOMAIN: 'Data', ISO_DOMAIN: 'Iso',
                  BACKUP_DOMAIN: 'Backup'}

# Metadata keys
DMDK_VERSION = "VERSION"
DMDK_SDUUID = "SDUUID"
DMDK_TYPE = "TYPE"
DMDK_ROLE = "ROLE"
DMDK_DESCRIPTION = "DESCRIPTION"
DMDK_CLASS = "CLASS"
DMDK_POOLS = "POOL_UUID"

# Lock related metadata keys
DMDK_LOCK_POLICY = 'LOCKPOLICY'
DMDK_LOCK_RENEWAL_INTERVAL_SEC = 'LOCKRENEWALINTERVALSEC'
DMDK_LEASE_TIME_SEC = 'LEASETIMESEC'
DMDK_IO_OP_TIMEOUT_SEC = 'IOOPTIMEOUTSEC'
DMDK_LEASE_RETRIES = 'LEASERETRIES'

DEFAULT_LEASE_PARAMS = {DMDK_LOCK_POLICY: "ON",
                        DMDK_LEASE_RETRIES: 3,
                        DMDK_LEASE_TIME_SEC: 60,
                        DMDK_LOCK_RENEWAL_INTERVAL_SEC: 5,
                        DMDK_IO_OP_TIMEOUT_SEC: 10}

MASTER_FS_DIR = 'master'
VMS_DIR = 'vms'
TASKS_DIR = 'tasks'

ImgsPar = namedtuple("ImgsPar", "imgs,parent")
ISO_IMAGE_UUID = '11111111-1111-1111-1111-111111111111'
BLANK_UUID = '00000000-0000-0000-0000-000000000000'
REMOVED_IMAGE_PREFIX = "_remove_me_"
ZEROED_IMAGE_PREFIX = REMOVED_IMAGE_PREFIX + "ZERO_"

# Blocks used for each lease (valid on all domain types)
LEASE_BLOCKS = 2048

UNICODE_MINIMAL_VERSION = 3

storage_repository = config.get('irs', 'repository')
mountBasePath = os.path.join(storage_repository, DOMAIN_MNT_POINT)


def getVolsOfImage(allVols, imgUUID):
    """ Filter allVols dict for volumes related to imgUUID.

    Returns {volName: (([templateImage], imgUUID, [otherImg]), volPar)
    For a template volume will be more than one image entry.

    allVols: The getAllVols() return dict.
    """

    return dict((volName, vol) for volName, vol in allVols.iteritems()
                if imgUUID in vol.imgs)


def supportsUnicode(version):
    return version >= UNICODE_MINIMAL_VERSION


# This method has strange semantics, it's only here to keep with the old
# behavior that someone might rely on.
def packLeaseParams(lockRenewalIntervalSec, leaseTimeSec,
                    ioOpTimeoutSec, leaseRetries):
    if (lockRenewalIntervalSec and leaseTimeSec and
            ioOpTimeoutSec and leaseRetries):
        return {DMDK_LEASE_RETRIES: leaseRetries,
                DMDK_LEASE_TIME_SEC: leaseTimeSec,
                DMDK_LOCK_RENEWAL_INTERVAL_SEC: lockRenewalIntervalSec,
                DMDK_IO_OP_TIMEOUT_SEC: ioOpTimeoutSec}

    return DEFAULT_LEASE_PARAMS


def validateDomainVersion(version):
    if version not in constants.SUPPORTED_DOMAIN_VERSIONS:
        raise se.UnsupportedDomainVersion(version)


def validateSDDeprecatedStatus(status):
    if not status.capitalize() in DEPRECATED_STATUSES:
        raise se.StorageDomainStatusError(status)
    return DEPRECATED_STATUSES[status.capitalize()]


def validateSDStatus(status):
    if not status.capitalize() in DOMAIN_STATUSES:
        raise se.StorageDomainStatusError(status)


def storageType(t):
    if isinstance(t, types.StringTypes):
        t = t.upper()
    if t in DOMAIN_TYPES.values():
        return t
    try:
        return type2name(int(t))
    except:
        raise se.StorageDomainTypeError(str(t))


def type2name(domType):
    return DOMAIN_TYPES[domType]


def name2type(name):
    for (k, v) in DOMAIN_TYPES.iteritems():
        if v == name.upper():
            return k
    raise KeyError(name)


def class2name(domClass):
    return DOMAIN_CLASSES[domClass]


def name2class(name):
    for (k, v) in DOMAIN_CLASSES.iteritems():
        if v == name:
            return k
    raise KeyError(name)


def getNamespace(*args):
    return '_'.join(args)


def sizeStr2Int(size_str):
    if size_str.endswith("M") or size_str.endswith("m"):
        size = int(size_str[:-1]) * (1 << 20)
    elif size_str.endswith("G") or size_str.endswith("g"):
        size = int(size_str[:-1]) * (1 << 30)
    else:
        size = int(size_str)

    return size


def intOrDefault(default, val):
    try:
        return int(val)
    except ValueError:
        return default


def intEncode(num):
    if num is None:
        return ""

    num = int(num)
    return str(num)


SD_MD_FIELDS = {
    # Key          dec,  enc
    DMDK_VERSION: (int, str),
    DMDK_SDUUID: (str, str),  # one day we might just use the uuid obj
    DMDK_TYPE: (name2type, type2name),  # They should throw exceptions
    DMDK_ROLE: (str, str),  # should be enum as well
    DMDK_DESCRIPTION: (unicodeDecoder, unicodeEncoder),
    DMDK_CLASS: (name2class, class2name),
    # one day maybe uuid
    DMDK_POOLS: (lambda s: s.split(",") if s else [],
                 lambda poolUUIDs: ",".join(poolUUIDs)),
    DMDK_LOCK_POLICY: (str, str),
    DMDK_LOCK_RENEWAL_INTERVAL_SEC: (
        lambda val: intOrDefault(
            DEFAULT_LEASE_PARAMS[DMDK_LOCK_RENEWAL_INTERVAL_SEC], val),
        intEncode),
    DMDK_LEASE_TIME_SEC: (
        lambda val: intOrDefault(
            DEFAULT_LEASE_PARAMS[DMDK_LEASE_TIME_SEC], val),
        intEncode),
    DMDK_IO_OP_TIMEOUT_SEC: (
        lambda val: intOrDefault(
            DEFAULT_LEASE_PARAMS[DMDK_IO_OP_TIMEOUT_SEC], val),
        intEncode),
    DMDK_LEASE_RETRIES: (
        lambda val: intOrDefault(
            DEFAULT_LEASE_PARAMS[DMDK_LEASE_RETRIES], val),
        intEncode),
}


class StorageDomainManifest(object):
    log = logging.getLogger("Storage.StorageDomainManifest")

    def __init__(self, sdUUID, domaindir):
        self.sdUUID = sdUUID
        self.domaindir = domaindir
        self._metadata = None

    @property
    def oop(self):
        return oop.getProcessPool(self.sdUUID)

    def replaceMetadata(self, md):
        self._metadata = md

    def getIsoDomainImagesDir(self):
        """
        Get 'images' directory from Iso domain
        """
        return os.path.join(self.domaindir, DOMAIN_IMAGES, ISO_IMAGE_UUID)

    def getMDPath(self):
        if self.domaindir:
            return os.path.join(self.domaindir, DOMAIN_META_DATA)
        return None

    def getMetaParam(self, key):
        return self._metadata[key]


class StorageDomain(object):
    log = logging.getLogger("Storage.StorageDomain")
    storage_repository = config.get('irs', 'repository')
    mdBackupVersions = config.get('irs', 'md_backup_versions')
    mdBackupDir = config.get('irs', 'md_backup_dir')

    # version: (clusterLockClass, hasVolumeLeases)
    _clusterLockTable = {
        0: (clusterlock.SafeLease, False),
        2: (clusterlock.SafeLease, False),
        3: (clusterlock.SANLock, True),
    }

    def __init__(self, manifest):
        self._manifest = manifest
        self._lock = threading.Lock()
        self.stat = None
        self._clusterLock = self._makeClusterLock()

    def __del__(self):
        if self.stat:
            threading.Thread(target=self.stat.stop).start()

    @property
    def sdUUID(self):
        return self._manifest.sdUUID

    @property
    def domaindir(self):
        return self._manifest.domaindir

    @property
    def _metadata(self):
        # TODO: Remove this once refactoring is complete and it has no callers
        return self._manifest._metadata

    @property
    def mountpoint(self):
        return self._manifest.mountpoint

    def replaceMetadata(self, md):
        """
        Used by FormatConverter to replace the metadata reader/writer
        """
        self._manifest.replaceMetadata(md)

    def getReadDelay(self):
        return self._manifest.getReadDelay()

    def getVSize(self, imgUUID, volUUID):
        return self._manifest.getVSize(imgUUID, volUUID)

    def prepareMailbox(self):
        """
        This method has been introduced in order to prepare the mailbox
        on those domains where the metadata for the inbox and outbox
        wasn't allocated on creation.
        """

    @property
    def supportsMailbox(self):
        return True

    @property
    def supportsSparseness(self):
        """
        This property advertises whether the storage domain supports
        sparseness or not.
        """
        return False

    @property
    def oop(self):
        return self._manifest.oop

    def _makeClusterLock(self, domVersion=None):
        if not domVersion:
            domVersion = self.getVersion()

        leaseParams = (
            DEFAULT_LEASE_PARAMS[DMDK_LOCK_RENEWAL_INTERVAL_SEC],
            DEFAULT_LEASE_PARAMS[DMDK_LEASE_TIME_SEC],
            DEFAULT_LEASE_PARAMS[DMDK_LEASE_RETRIES],
            DEFAULT_LEASE_PARAMS[DMDK_IO_OP_TIMEOUT_SEC],
        )

        try:
            clusterLockClass = self._clusterLockTable[domVersion][0]
        except KeyError:
            raise se.UnsupportedDomainVersion(domVersion)

        return clusterLockClass(self.sdUUID, self.getIdsFilePath(),
                                self.getLeasesFilePath(), *leaseParams)

    @classmethod
    def create(cls, sdUUID, domainName, domClass, typeSpecificArg, version):
        """
        Create a storage domain. The initial status is unattached.
        The storage domain underlying storage must be visible (connected)
        at that point.
        """
        pass

    def _registerResourceNamespaces(self):
        """
        Register resources namespaces and create
        factories for it.
        """
        rmanager = rm.ResourceManager.getInstance()
        # Register image resource namespace
        imageResourceFactory = \
            resourceFactories.ImageResourceFactory(self.sdUUID)
        imageResourcesNamespace = getNamespace(self.sdUUID, IMAGE_NAMESPACE)
        try:
            rmanager.registerNamespace(imageResourcesNamespace,
                                       imageResourceFactory)
        except KeyError:
            self.log.info("Resource namespace %s already registered",
                          imageResourcesNamespace)

        volumeResourcesNamespace = getNamespace(self.sdUUID, VOLUME_NAMESPACE)
        try:
            rmanager.registerNamespace(volumeResourcesNamespace,
                                       rm.SimpleResourceFactory())
        except KeyError:
            self.log.info("Resource namespace %s already registered",
                          volumeResourcesNamespace)

    def produceVolume(self, imgUUID, volUUID):
        """
        Produce a type specific volume object
        """
        return self.getVolumeClass()(self.mountpoint, self.sdUUID, imgUUID,
                                     volUUID)

    def getVolumeClass(self):
        """
        Return a type specific volume generator object
        """
        pass

    def validateCreateVolumeParams(self, volFormat, srcVolUUID,
                                   preallocate=None):
        """
        Validate create volume parameters
        """
        if volFormat not in volume.VOL_FORMAT:
            raise se.IncorrectFormat(volFormat)

        # Volumes with a parent must be cow
        if srcVolUUID != volume.BLANK_UUID and volFormat != volume.COW_FORMAT:
            raise se.IncorrectFormat(volume.type2name(volFormat))

        if preallocate is not None and preallocate not in volume.VOL_TYPE:
            raise se.IncorrectType(preallocate)

    def createVolume(self, imgUUID, size, volFormat, preallocate, diskType,
                     volUUID, desc, srcImgUUID, srcVolUUID, initialSize=None):
        """
        Create a new volume
        """
        return self.getVolumeClass().create(
            self._getRepoPath(), self.sdUUID, imgUUID, size, volFormat,
            preallocate, diskType, volUUID, desc, srcImgUUID, srcVolUUID,
            initialSize=initialSize)

    def getMDPath(self):
        return self._manifest.getMDPath()

    def initSPMlease(self):
        """
        Initialize the SPM lease
        """
        try:
            self._clusterLock.initLock()
            self.log.debug("lease initialized successfully")
        except:
            # Original code swallowed the errors
            self.log.warn("lease did not initialize successfully",
                          exc_info=True)

    def getVersion(self):
        return self.getMetaParam(DMDK_VERSION)

    def getFormat(self):
        return str(self.getVersion())

    def getPools(self):
        try:
            pools = self.getMetaParam(key=DMDK_POOLS)
        except KeyError:
            pools = []
        else:
            # Old pool MD marked SDs not belonging to any pool with
            # BLANK_UUID as the pool uuid.
            if BLANK_UUID in pools:
                pools.remove(BLANK_UUID)
        return pools

    def getIdsFilePath(self):
        return self._manifest.getIdsFilePath()

    def getLeasesFilePath(self):
        return self._manifest.getLeasesFilePath()

    def getReservedId(self):
        return self._clusterLock.getReservedId()

    def acquireHostId(self, hostId, async=False):
        self._clusterLock.acquireHostId(hostId, async)

    def releaseHostId(self, hostId, async=False, unused=False):
        self._clusterLock.releaseHostId(hostId, async, unused)

    def hasHostId(self, hostId):
        return self._clusterLock.hasHostId(hostId)

    def getHostStatus(self, hostId):
        return self._clusterLock.getHostStatus(hostId)

    def hasVolumeLeases(self):
        domVersion = self.getVersion()
        try:
            return self._clusterLockTable[domVersion][1]
        except KeyError:
            raise se.UnsupportedDomainVersion(domVersion)

    def getVolumeLease(self, volUUID):
        """
        Return the volume lease (leasePath, leaseOffset)
        """
        return None, None

    def acquireClusterLock(self, hostID):
        self.refresh()
        self._clusterLock.setParams(
            self.getMetaParam(DMDK_LOCK_RENEWAL_INTERVAL_SEC),
            self.getMetaParam(DMDK_LEASE_TIME_SEC),
            self.getMetaParam(DMDK_LEASE_RETRIES),
            self.getMetaParam(DMDK_IO_OP_TIMEOUT_SEC)
        )
        self._clusterLock.acquire(hostID)

    def releaseClusterLock(self):
        self._clusterLock.release()

    def inquireClusterLock(self):
        return self._clusterLock.inquire()

    def attach(self, spUUID):
        self.invalidateMetadata()
        pools = self.getPools()
        if spUUID in pools:
            self.log.warn("domain `%s` is already attached to pool `%s`",
                          self.sdUUID, spUUID)
            return

        if len(pools) > 0 and not self.isISO():
            raise se.StorageDomainAlreadyAttached(pools[0], self.sdUUID)

        pools.append(spUUID)
        self.setMetaParam(DMDK_POOLS, pools)

    def detach(self, spUUID):
        self.log.info('detaching storage domain %s from pool %s',
                      self.sdUUID, spUUID)
        self.invalidateMetadata()
        pools = self.getPools()
        try:
            pools.remove(spUUID)
        except ValueError:
            self.log.error(
                "Can't remove pool %s from domain %s pool list %s, "
                "it does not exist",
                spUUID, self.sdUUID, str(pools))
            return
        # Make sure that ROLE is not MASTER_DOMAIN (just in case)
        with self._metadata.transaction():
            self.changeRole(REGULAR_DOMAIN)
            self.setMetaParam(DMDK_POOLS, pools)
        # Last thing to do is to remove pool from domain
        # do any required cleanup

    # I personally don't think there is a reason to pack these
    # but I already changed too much.
    def changeLeaseParams(self, leaseParamPack):
        self.setMetaParams(leaseParamPack)

    def getLeaseParams(self):
        keys = [DMDK_LOCK_RENEWAL_INTERVAL_SEC, DMDK_LEASE_TIME_SEC,
                DMDK_IO_OP_TIMEOUT_SEC, DMDK_LEASE_RETRIES]
        params = {}
        for key in keys:
            params[key] = self.getMetaParam(key)
        return params

    def getMasterDir(self):
        return os.path.join(self.domaindir, MASTER_FS_DIR)

    def invalidate(self):
        """
        Make sure that storage domain is inaccessible
        """
        pass

    def validateMaster(self):
        """Validate that the master storage domain is correct.
        """
        stat = {'mount': True, 'valid': True}
        if not self.isMaster():
            return stat

        # If the host is SPM then at this point masterFS should be mounted
        # In HSM case we can return False and then upper logic should handle it
        if not self.validateMasterMount():
            stat['mount'] = False
            return stat

        pdir = self.getVMsDir()
        if not self.oop.fileUtils.pathExists(pdir):
            stat['valid'] = False
            return stat
        pdir = self.getTasksDir()
        if not self.oop.fileUtils.pathExists(pdir):
            stat['valid'] = False
            return stat

        return stat

    def getVMsDir(self):
        return os.path.join(self.domaindir, MASTER_FS_DIR, VMS_DIR)

    def getTasksDir(self):
        return os.path.join(self.domaindir, MASTER_FS_DIR, TASKS_DIR)

    def getVMsList(self):
        vmsPath = self.getVMsDir()
        # find out VMs list
        VM_PATTERN = os.path.join(vmsPath, constants.UUID_GLOB_PATTERN)
        vms = self.oop.glob.glob(VM_PATTERN)
        vmList = [os.path.basename(i) for i in vms]
        self.log.info("vmList=%s", str(vmList))

        return vmList

    def getVMsInfo(self, vmList=None):
        """
        Get list of VMs with their info from the pool.
        If 'vmList' are given get info of these VMs only
        """

        vmsInfo = {}
        vmsPath = self.getVMsDir()

        # Find out relevant VMs
        if not vmList:
            vmList = self.getVMsList()

        self.log.info("vmList=%s", str(vmList))

        for vm in vmList:
            vm_path = os.path.join(vmsPath, vm)
            # If VM doesn't exist, ignore it silently
            if not os.path.exists(vm_path):
                continue
            ovfPath = os.path.join(vm_path, vm + '.ovf')
            if not os.path.lexists(ovfPath):
                raise se.MissingOvfFileFromVM(vm)

            ovf = codecs.open(ovfPath, encoding='utf8').read()
            vmsInfo[vm] = ovf

        return vmsInfo

    def createMasterTree(self):
        """
        Make tasks and vms directories on master directory.
        """
        vmsDir = self.getVMsDir()
        self.log.debug("creating vms dir: %s" % vmsDir)
        self.oop.fileUtils.createdir(vmsDir)
        tasksDir = self.getTasksDir()
        self.log.debug("creating task dir: %s" % tasksDir)
        self.oop.fileUtils.createdir(tasksDir)

    def activate(self):
        """
        Activate a storage domain that is already a member in a storage pool.
        """
        if self.isBackup():
            self.mountMaster()
            self.createMasterTree()

    def _getRepoPath(self):
        # This is here to make sure no one tries to get a repo
        # path from an ISO domain.
        if self.getDomainClass() == ISO_DOMAIN:
            raise se.ImagesNotSupportedError()

        # If it has a repo we don't have multiple domains. Assume single pool
        return os.path.join(self.storage_repository, self.getPools()[0])

    def getLinkBCImagePath(self, imgUUID):
        return image.Image(self._getRepoPath()) \
                    .getImageDir(self.sdUUID, imgUUID)

    def getImageRundir(self, imgUUID):
        return os.path.join(constants.P_VDSM_STORAGE, self.sdUUID, imgUUID)

    def getIsoDomainImagesDir(self):
        return self._manifest.getIsoDomainImagesDir()

    def supportsUnicode(self):
        return supportsUnicode(self.getVersion())

    def setDescription(self, descr):
        """
        Set storage domain description
            'descr' - domain description
        """
        self.log.info("sdUUID=%s descr=%s", self.sdUUID, descr)
        if not misc.isAscii(descr) and not self.supportsUnicode():
            raise se.UnicodeArgumentException()

        self.setMetaParam(DMDK_DESCRIPTION, descr)

    def getInfo(self):
        """
        Get storage domain info
        """
        info = {}
        info['uuid'] = self.sdUUID
        info['type'] = type2name(self.getMetaParam(DMDK_TYPE))
        info['class'] = class2name(self.getMetaParam(DMDK_CLASS))
        info['name'] = self.getMetaParam(DMDK_DESCRIPTION)
        info['role'] = self.getMetaParam(DMDK_ROLE)
        info['pool'] = self.getPools()
        info['version'] = str(self.getMetaParam(DMDK_VERSION))
        return info

    def getStats(self):
        """
        """
        pass

    def mountMaster(self):
        """
        Mount the master metadata file system. Should be called only by SPM.
        """
        pass

    def unmountMaster(self):
        """
        Unmount the master metadata file system. Should be called only by SPM.
        """
        pass

    def extendVolume(self, volumeUUID, size, isShuttingDown=None):
        pass

    def getMetadata(self):
        """
        Unified Metadata accessor/mutator
        """
        return self._metadata.copy()

    def setMetadata(self, newMetadata):
        # Backup old md (rotate old backup files)
        misc.rotateFiles(self.mdBackupDir, self.sdUUID, self.mdBackupVersions)
        oldMd = ["%s=%s\n" % (key, value)
                 for key, value in self.getMetadata().copy().iteritems()]
        with open(os.path.join(self.mdBackupDir, self.sdUUID), "w") as f:
            f.writelines(oldMd)

        with self._metadata.transaction():
            self._metadata.clear()
            self._metadata.update(newMetadata)

    def invalidateMetadata(self):
        self._metadata.invalidate()

    def getMetaParam(self, key):
        return self._manifest.getMetaParam(key)

    def getStorageType(self):
        return self.getMetaParam(DMDK_TYPE)

    def getDomainRole(self):
        return self.getMetaParam(DMDK_ROLE)

    def getDomainClass(self):
        return self.getMetaParam(DMDK_CLASS)

    def getRemotePath(self):
        pass

    def templateRelink(self, imgUUID, volUUID):
        """
        Relink all hardlinks of the template 'volUUID' in all VMs based on it.
        No need to relink template for block domains.
        """
        self.log.debug("Skipping relink of template, domain %s is not file "
                       "based", self.sdUUID)

    def changeRole(self, newRole):
        # TODO: Move to a validator?
        if newRole not in [REGULAR_DOMAIN, MASTER_DOMAIN]:
            raise ValueError(newRole)

        self.setMetaParam(DMDK_ROLE, newRole)

    def setMetaParams(self, params):
        self._metadata.update(params)

    def setMetaParam(self, key, value):
        """
        Set new meta data KEY=VALUE pair
        """
        self.setMetaParams({key: value})

    def refreshDirTree(self):
        pass

    def refresh(self):
        pass

    def extend(self, devlist, force):
        pass

    def resizePV(self, guid):
        pass

    def isMaster(self):
        return self.getMetaParam(DMDK_ROLE).capitalize() == MASTER_DOMAIN

    def initMaster(self, spUUID, leaseParams):
        self.invalidateMetadata()
        pools = self.getPools()

        if len(pools) > 1 or (len(pools) == 1 and pools[0] != spUUID):
            raise se.StorageDomainAlreadyAttached(pools[0], self.sdUUID)

        with self._metadata.transaction():
            self.changeLeaseParams(leaseParams)
            self.setMetaParam(DMDK_POOLS, [spUUID])
            self.changeRole(MASTER_DOMAIN)

    def isISO(self):
        return self.getMetaParam(DMDK_CLASS) == ISO_DOMAIN

    def isBackup(self):
        return self.getMetaParam(DMDK_CLASS) == BACKUP_DOMAIN

    def isData(self):
        return self.getMetaParam(DMDK_CLASS) == DATA_DOMAIN

    def imageGarbageCollector(self):
        """
        Image Garbage Collector
        remove the remnants of the removed images (they could be left sometimes
        (on NFS mostly) due to lazy file removal
        """
        pass
