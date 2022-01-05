#
# Copyright 2009-2017 Red Hat, Inc.
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

import os
import logging
import threading
import time
from collections import namedtuple
import codecs
from contextlib import contextmanager

import six

from vdsm import host
from vdsm import utils
from vdsm.common import exception
from vdsm.common.marks import deprecated
from vdsm.common.threadlocal import vars
from vdsm.common.units import MiB, GiB
from vdsm.config import config
from vdsm.storage import clusterlock
from vdsm.storage import constants as sc
from vdsm.storage import exception as se
from vdsm.storage import fileUtils
from vdsm.storage import guarded
from vdsm.storage import misc
from vdsm.storage import outOfProcess as oop
from vdsm.storage import qemuimg
from vdsm.storage import resourceFactories
from vdsm.storage import resourceManager as rm
from vdsm.storage import rwlock
from vdsm.storage import sanlock_direct
from vdsm.storage import task
from vdsm.storage import utils as su
from vdsm.storage import validators
from vdsm.storage import xlease
from vdsm.storage.sdc import sdCache

from vdsm.storage.persistent import unicodeEncoder, unicodeDecoder

DOMAIN_META_DATA = 'dom_md'
DOMAIN_IMAGES = 'images'
# Domain's metadata volume name
METADATA = "metadata"
# Domain metadata slot size (it always takes the first slot)
MAX_DOMAIN_DESCRIPTION_SIZE = 50

GLUSTERSD_DIR = "glusterSD"

BLOCKSD_DIR = "blockSD"
LEASES = "leases"
IDS = "ids"
INBOX = "inbox"
OUTBOX = "outbox"

# External leases volume for vm leases and other leases not attached to
# volumes.
XLEASES = "xleases"

# The size of these volumes is calculated dynamically based on the
# domain block size and alignment.
LEASES_SLOTS = 2048
XLEASES_SLOTS = 1024

# Special volumes available since storage domain version 0
SPECIAL_VOLUMES_V0 = (METADATA, LEASES, IDS, INBOX, OUTBOX)

# Special volumes available since storage domain version 4.
SPECIAL_VOLUMES_V4 = SPECIAL_VOLUMES_V0 + (XLEASES,)

SPECIAL_VOLUME_SIZES_MIB = {
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
DMDK_BLOCK_SIZE = "BLOCK_SIZE"
DMDK_ALIGNMENT = "ALIGNMENT"

# Lock related metadata keys
DMDK_LOCK_POLICY = 'LOCKPOLICY'
DMDK_LOCK_RENEWAL_INTERVAL_SEC = 'LOCKRENEWALINTERVALSEC'
DMDK_LEASE_TIME_SEC = 'LEASETIMESEC'
DMDK_IO_OP_TIMEOUT_SEC = 'IOOPTIMEOUTSEC'
DMDK_LEASE_RETRIES = 'LEASERETRIES'

# Keys used only on block storage domain before v5.
DMDK_LOGBLKSIZE = "LOGBLKSIZE"
DMDK_PHYBLKSIZE = "PHYBLKSIZE"

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

UNICODE_MINIMAL_VERSION = 3

# The LEASE_SLOT is used by Sanlock to not overlap with safelease in
# order to preserve the ability to acquire both locks during the domain
# upgrade from V1 to V3.
SDM_LEASE_NAME = 'SDM'
SDM_LEASE_SLOT = 1

# Reserved leases for special purposes:
#  - 0       SPM (Backward comapatibility with V0 and V2)
#  - 1       SDM (SANLock V3)
#  - 2..100  (Unassigned)
RESERVED_LEASES = 100

VolumeSize = namedtuple("VolumeSize", [
    # The logical volume size in block storage and file size in file
    # storage.
    "apparentsize",

    # The allocated size on storage. Same as apparentsize in block
    # storage.
    "truesize",
])


def getVolsOfImage(allVols, imgUUID):
    """ Filter allVols dict for volumes related to imgUUID.

    Returns {volName: (([templateImage], imgUUID, [otherImg]), volPar)
    For a template volume will be more than one image entry.

    allVols: The getAllVols() return dict.
    """

    return dict((volName, vol) for volName, vol in six.iteritems(allVols)
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


def validateSDDeprecatedStatus(status):
    if not status.capitalize() in DEPRECATED_STATUSES:
        raise se.StorageDomainStatusError(status)
    return DEPRECATED_STATUSES[status.capitalize()]


def validateSDStatus(status):
    if not status.capitalize() in DOMAIN_STATUSES:
        raise se.StorageDomainStatusError(status)


def storageType(t):
    if isinstance(t, (six.text_type, six.binary_type)):
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
    for (k, v) in six.iteritems(DOMAIN_TYPES):
        if v == name.upper():
            return k
    raise KeyError(name)


def class2name(domClass):
    return DOMAIN_CLASSES[domClass]


def name2class(name):
    for (k, v) in six.iteritems(DOMAIN_CLASSES):
        if v == name:
            return k
    raise KeyError(name)


def sizeStr2Int(size_str):
    if size_str.endswith("M") or size_str.endswith("m"):
        size = int(size_str[:-1]) * MiB
    elif size_str.endswith("G") or size_str.endswith("g"):
        size = int(size_str[:-1]) * GiB
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
    DMDK_BLOCK_SIZE: (int, str),
    DMDK_ALIGNMENT: (int, str),
}


class StorageDomainManifest(object):
    log = logging.getLogger("storage.storagedomainmanifest")
    mountpoint = None

    # version: clusterLockClass
    _domainLockTable = {
        0: clusterlock.SafeLease,
        2: clusterlock.SafeLease,
        3: clusterlock.SANLock,
        4: clusterlock.SANLock,
        5: clusterlock.SANLock,
    }

    def __init__(self, sdUUID, domaindir, metadata):
        self.sdUUID = sdUUID
        self.domaindir = domaindir
        self.replaceMetadata(metadata)
        self._external_leases_lock = rwlock.RWLock()
        self._alignment = metadata.get(DMDK_ALIGNMENT, sc.ALIGNMENT_1M)
        self._block_size = metadata.get(DMDK_BLOCK_SIZE, sc.BLOCK_SIZE_512)

        # Validate alignment and block size.

        version = self.getVersion()
        if version < 5:
            if self.alignment != sc.ALIGNMENT_1M:
                raise se.MetaDataValidationError(
                    "Storage domain version {} does not support alignment {}"
                        .format(version, self.alignment))

            if self.block_size != sc.BLOCK_SIZE_512:
                raise se.MetaDataValidationError(
                    "Storage domain version {} does not support block size {}"
                        .format(version, self.block_size))

        self._domainLock = self._makeDomainLock()

    @classmethod
    def special_volumes(cls, version):
        """
        Return the special volumes managed by this storage domain.
        """
        raise NotImplementedError

    @property
    def supportsSparseness(self):
        """
        This property advertises whether the storage domain supports
        sparseness or not.
        """
        return False

    @property
    def supports_inquire(self):
        """
        This property advertises whether the storage domain supports
        inquireCluserLock().
        """
        return self._domainLock.supports_inquire

    def recommends_unordered_writes(self, format):
        """
        Return True if unordered writes are recommended for copying an image
        using format to this storage domain.

        Unordered writes improve copy performance but are recommended only for
        preallocated devices and raw format.
        """
        return format == sc.RAW_FORMAT and not self.supportsSparseness

    @property
    def oop(self):
        return oop.getProcessPool(self.sdUUID)

    def qcow2_compat(self):
        if self.getVersion() >= 4:
            return "1.1"
        return qemuimg.default_qcow2_compat()

    def supports_qcow2_compat(self, value):
        if self.getVersion() >= 4:
            return qemuimg.supports_compat(value)
        else:
            return value in ("0.10", qemuimg.default_qcow2_compat())

    def supports_device_reduce(self):
        return False

    def replaceMetadata(self, md):
        self._metadata = md

    def getDomainRole(self):
        return self.getMetaParam(DMDK_ROLE)

    def getDomainClass(self):
        return self.getMetaParam(DMDK_CLASS)

    def getStorageType(self):
        return self.getMetaParam(DMDK_TYPE)

    def getRepoPath(self):
        # This is here to make sure no one tries to get a repo
        # path from an ISO domain.
        if self.getDomainClass() == ISO_DOMAIN:
            raise se.ImagesNotSupportedError()

        # Get the datacenter ID.  When using storage pools this will be the
        # spUUID.  Else, it's just a UUID to establish a storage namespace.
        return os.path.join(sc.REPO_DATA_CENTER, self.getPools()[0])

    def getImageDir(self, imgUUID):
        return os.path.join(self.domaindir, DOMAIN_IMAGES, imgUUID)

    def getIsoDomainImagesDir(self):
        """
        Get 'images' directory from Iso domain
        """
        return os.path.join(self.domaindir, DOMAIN_IMAGES, ISO_IMAGE_UUID)

    def getMDPath(self):
        if self.domaindir:
            return os.path.join(self.domaindir, DOMAIN_META_DATA)
        return None

    def getMetadata(self):
        """
        Unified Metadata accessor/mutator
        """
        return self._metadata.copy()

    def getMetaParam(self, key):
        return self._metadata[key]

    def getVersion(self):
        try:
            version = self.getMetaParam(DMDK_VERSION)
        except KeyError:
            raise se.InvalidMetadata("key={}".format(DMDK_VERSION))
        return version

    @property
    def alignment(self):
        return self._alignment

    @property
    def block_size(self):
        return self._block_size

    def resizePV(self, guid):
        pass

    def movePV(self, src_device, dst_devices):
        raise exception.UnsupportedOperation()

    def reduceVG(self, guid):
        raise exception.UnsupportedOperation()

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
        raise NotImplementedError

    def getLeasesFilePath(self):
        raise NotImplementedError

    def produceVolume(self, imgUUID, volUUID):
        """
        Produce a type specific VolumeManifest object
        """
        return self.getVolumeClass()(self.mountpoint, self.sdUUID, imgUUID,
                                     volUUID)

    def isISO(self):
        return self.getMetaParam(DMDK_CLASS) == ISO_DOMAIN

    def isBackup(self):
        return self.getMetaParam(DMDK_CLASS) == BACKUP_DOMAIN

    def isData(self):
        return self.getMetaParam(DMDK_CLASS) == DATA_DOMAIN

    def getReservedId(self):
        return self._domainLock.getReservedId()

    def acquireHostId(self, hostId, wait=True):
        self._domainLock.acquireHostId(hostId, wait)

    def releaseHostId(self, hostId, wait=True, unused=False):
        self._domainLock.releaseHostId(hostId, wait, unused)

    def hasHostId(self, hostId):
        return self._domainLock.hasHostId(hostId)

    def getHostStatus(self, hostId):
        return self._domainLock.getHostStatus(hostId)

    def hasVolumeLeases(self):
        return self._domainLock.supports_multiple_leases

    def getVolumeLease(self, imgUUID, volUUID):
        """
        Return the volume lease (leasePath, leaseOffset)
        """
        return clusterlock.Lease(None, None, None)

    def acquireVolumeLease(self, hostId, imgUUID, volUUID):
        lease = self.getVolumeLease(imgUUID, volUUID)
        self._domainLock.acquire(hostId, lease)

    def releaseVolumeLease(self, imgUUID, volUUID):
        lease = self.getVolumeLease(imgUUID, volUUID)
        self._domainLock.release(lease)

    def inspectVolumeLease(self, imgUUID, volUUID):
        lease = self.getVolumeLease(imgUUID, volUUID)
        return self._domainLock.inspect(lease)

    def getDomainLease(self):
        """
        Return the domain lease.

        This lease is used by the SPM to protect metadata operations in the
        cluster.
        """
        return clusterlock.Lease(SDM_LEASE_NAME,
                                 self.getLeasesFilePath(),
                                 SDM_LEASE_SLOT * self.alignment)

    def acquireDomainLock(self, hostID):
        self.refresh()
        self._domainLock.setParams(
            self.getMetaParam(DMDK_LOCK_RENEWAL_INTERVAL_SEC),
            self.getMetaParam(DMDK_LEASE_TIME_SEC),
            self.getMetaParam(DMDK_LEASE_RETRIES),
            self.getMetaParam(DMDK_IO_OP_TIMEOUT_SEC)
        )
        self._domainLock.acquire(hostID, self.getDomainLease())

    def releaseDomainLock(self):
        self._domainLock.release(self.getDomainLease())

    @contextmanager
    def domain_lock(self, host_id):
        self.acquireDomainLock(host_id)
        try:
            yield
        finally:
            self.releaseDomainLock()

    @contextmanager
    def domain_id(self, host_id):
        self.acquireHostId(host_id)
        try:
            yield
        finally:
            self.releaseHostId(host_id)

    def inspectDomainLock(self):
        return self._domainLock.inspect(self.getDomainLease())

    def inquireDomainLock(self):
        return self._domainLock.inquire()

    def _makeDomainLock(self, domVersion=None):
        if not domVersion:
            domVersion = self.getVersion()

        try:
            lockClass = self._domainLockTable[domVersion]
        except KeyError:
            raise se.UnsupportedDomainVersion(domVersion)

        # Note: lease and leaseParams are needed only for legacy locks
        # supporting only single lease, and ignored by modern lock managers
        # like sanlock. On the contrary, kwargs are not needed by legacy locks
        # and are used by modern locks like sanlock.

        leaseParams = (
            DEFAULT_LEASE_PARAMS[DMDK_LOCK_RENEWAL_INTERVAL_SEC],
            DEFAULT_LEASE_PARAMS[DMDK_LEASE_TIME_SEC],
            DEFAULT_LEASE_PARAMS[DMDK_LEASE_RETRIES],
            DEFAULT_LEASE_PARAMS[DMDK_IO_OP_TIMEOUT_SEC],
        )

        kwargs = {
            "alignment": self._alignment,
            "block_size": self._block_size,
        }

        return lockClass(self.sdUUID, self.getIdsFilePath(),
                         self.getDomainLease(), *leaseParams, **kwargs)

    def initDomainLock(self):
        """
        Initialize the SPM lease
        """
        self._domainLock.initLock(self.getDomainLease())
        self.log.debug("lease initialized successfully")

    def refreshDirTree(self):
        pass

    def refresh(self):
        pass

    def validateCreateVolumeParams(self, volFormat, srcVolUUID, diskType=None,
                                   preallocate=None, add_bitmaps=False):
        """
        Validate create volume parameters
        """
        if volFormat not in sc.VOL_FORMAT:
            raise se.IncorrectFormat(volFormat)

        # Volumes with a parent must be cow
        if srcVolUUID != sc.BLANK_UUID and volFormat != sc.COW_FORMAT:
            raise se.IncorrectFormat(sc.type2name(volFormat))

        if diskType is not None and diskType not in sc.VOL_DISKTYPE:
            raise se.InvalidParameterException("DiskType", diskType)

        if preallocate is not None and preallocate not in sc.VOL_TYPE:
            raise se.IncorrectType(preallocate)

        if add_bitmaps:
            if srcVolUUID == sc.BLANK_UUID:
                raise se.UnsupportedOperation(
                    "Cannot add bitmaps for volume without parent volume",
                    srcVolUUID=srcVolUUID,
                    add_bitmaps=add_bitmaps)

            if not self.supports_bitmaps_operations():
                raise se.UnsupportedOperation(
                    "Cannot perform bitmaps operations on "
                    "storage domain version < 4",
                    domain_version=self.getVersion(),
                    add_bitmaps=add_bitmaps)

    def teardownVolume(self, imgUUID, volUUID):
        """
        Called when a volume is detached from a prepared image during live
        merge flow. In this case, the volume will not be torn down when
        the image is torn down.
        This does nothing, subclass should override this if needed.
        """

    def getVolumeClass(self):
        """
        Return a type specific volume generator object
        """
        raise NotImplementedError

    def supports_bitmaps_operations(self):
        """
        Return True if this domain supports bitmaps operations,
        False otherwise.
        """
        return self.getVersion() >= 4

    # External leases support

    @classmethod
    def supports_external_leases(cls, version):
        """
        Return True if this domain supports external leases, False otherwise.
        """
        return version >= 4

    @classmethod
    @contextmanager
    def external_leases_backend(cls, lockspace, path):
        """
        Return a context manager for performing I/O to the extenal leases
        volume.

        Arguments:
            lockspace (str): Sanlock lockspace name, storage domain uuid.
            path (str): Path to the external leases volume

        Returns:
            context manager.
        """
        backend = xlease.DirectFile(path)
        with utils.closing(backend):
            yield backend

    def external_leases_path(self):
        """
        Return the path to the external leases volume.
        """
        raise NotImplementedError

    @property
    def external_leases_lock(self):
        """
        Return the external leases readers-writer lock.
        """
        return self._external_leases_lock

    @contextmanager
    def external_leases_volume(self):
        """
        Context manager returning the external leases volume.

        The caller is responsible for holding the external_leases_lock in the
        correct mode.
        """
        path = self.external_leases_path()
        with self.external_leases_backend(self.sdUUID, path) as backend:
            vol = xlease.LeasesVolume(
                backend,
                alignment=self._alignment,
                block_size=self._block_size)
            with utils.closing(vol):
                yield vol

    def acquire_external_lease(self, lease_id, host_id):
        """
        Acquire an external lease
        """
        lease_info = self.lease_info(lease_id)
        lease = clusterlock.Lease(
            lease_info.resource,
            lease_info.path,
            lease_info.offset)
        self._domainLock.acquire(host_id, lease, lvb=True)

    def release_external_lease(self, lease_id):
        """
        Release an external lease
        """
        lease_info = self.lease_info(lease_id)
        lease = clusterlock.Lease(
            lease_info.resource,
            lease_info.path,
            lease_info.offset)
        self._domainLock.release(lease)

    def lease_info(self, lease_id):
        """
        Return information about external lease that can be used to acquire or
        release the lease.

        May be called on any host.
        """
        with self.external_leases_lock.shared:
            with self.external_leases_volume() as vol:
                return vol.lookup(lease_id)

    def lease_status(self, lease_id, host_id):
        """
        Return the status of an external lease, indicating whether it is
        currently held.

        Returns:
            A dict containing information about the lease, such as:
            owners - A list of host ids holding the lease
            version - The version of the host, a lease's owner isa combination
                      of a host id and a version (controlled by sanlock)
            metadata - The lvb data stored on the lease.

        """
        lease_info = self.lease_info(lease_id)
        lease = clusterlock.Lease(
            lease_info.resource,
            lease_info.path,
            lease_info.offset)

        # Failing here will raise an exception, this expected as we cannot
        # tell anything about the status of the lease if we couldn't inspect
        # it.
        res_version, owner_host_id = self._domainLock.inspect(lease)
        lvb = None

        # We only care about reading lvb on released leases, as lvb is written
        # when the lease is released and we'll fail to acquire the lease if
        # it's held anyway.
        if owner_host_id is None:
            try:
                self.acquire_external_lease(lease_id, host_id)
            except se.AcquireLockFailure:
                self.log.warn("Could not acquire lease %s", lease_id)
                # lease is currently held, return lease info without lvb
            else:
                try:
                    lvb = self.get_lvb(lease_id)
                finally:
                    self.release_external_lease(lease_id)

        owners = [owner_host_id] if owner_host_id is not None else []

        response = {
            "owners": owners,
            "version": res_version,
        }

        if lvb is not None:
            response["metadata"] = lvb

        return response

    def set_lvb(self, lease_id, info):
        """
        Write LVB data for lease.
        Note: Lease must be first acquired with lvb=True

        Arguments:
            lease_id (str): uuid of the lease
            info (dict): info to write to the LVB of the lease
        """
        lease_info = self.lease_info(lease_id)
        lease = clusterlock.Lease(
            lease_info.resource,
            lease_info.path,
            lease_info.offset)

        self._domainLock.set_lvb(lease, info)

    def get_lvb(self, lease_id):
        """
        Read LVB data for lease.
        Note: Lease must be first acquired with lvb=True

        Arguments:
            lease_id (str): uuid of the lease
        Returns:
            A dict containing the data read from LVB.
        """
        lease_info = self.lease_info(lease_id)
        lease = clusterlock.Lease(
            lease_info.resource,
            lease_info.path,
            lease_info.offset)

        return self._domainLock.get_lvb(lease)

    def fence_lease(self, lease_id, host_id, metadata):
        """
        Fence a lease by updating the metadata based on the job status:
        * If the job is no longer running - If the lease is free, the job
          status is PENDING and the generation is correct, we can safely
          change the job status to FENCED and bump the generation.
        * If the job is still running, we will fail to acquire the lease,
          a sanlock error will be raised.
        * If the lease is free but the job is in a status other than PENDING
          (SUCCEEDED, FAILED), JobStatusMismatch will be raised.

        Arguments:
            lease_id (str): uuid of the lease.
            host_id (int): the id of the host attempting to fence.
            metadata (dict): expected lease metadata.
        """

        lease_info = self.lease_info(lease_id)
        lease = clusterlock.Lease(
            lease_info.resource,
            lease_info.path,
            lease_info.offset)
        self.acquire_external_lease(lease_id, host_id)
        try:
            current_metadata = self.get_lvb(lease_id)
            self.log.info(
                "Current lease %s metadata: %r", lease_id, metadata)

            if current_metadata.get("type") != metadata.type:
                raise se.UnsupportedOperation(
                    "job type doesn't match supported type",
                    expected=metadata.type,
                    actual=current_metadata.get("type"))

            if current_metadata.get("job_id") != metadata.job_id:
                raise se.UnsupportedOperation(
                    "job_id on lease doesn't match passed job_id",
                    exptected=metadata.job_id,
                    actual=current_metadata.get("job_id"))

            if current_metadata.get("job_status") != metadata.job_status:
                raise se.JobStatusMismatch(
                    metadata.job_status, current_metadata.get("job_status"))

            if current_metadata.get("generation") != metadata.generation:
                raise se.GenerationMismatch(
                    metadata.generation, current_metadata.get("generation"))

            updated_metadata = current_metadata.copy()
            updated_metadata["modified"] = int(time.time())
            updated_metadata["host_hardware_id"] = host.uuid()
            updated_metadata["generation"] = \
                su.next_generation(metadata.generation)
            updated_metadata["job_status"] = sc.JOB_STATUS_FENCED

            self.log.info(
                "Writing data to lease %s: %r", lease_id, updated_metadata)
            self._domainLock.set_lvb(lease, updated_metadata)
        finally:
            self.release_external_lease(lease_id)


class StorageDomain(object):
    log = logging.getLogger("storage.storagedomain")
    mdBackupVersions = config.get('irs', 'md_backup_versions')
    mdBackupDir = config.get('irs', 'md_backup_dir')
    manifestClass = StorageDomainManifest

    supported_block_size = ()
    # Default supported domain versions unless overidden
    supported_versions = sc.SUPPORTED_DOMAIN_VERSIONS

    def __init__(self, manifest):
        self._manifest = manifest
        # Do not allow attaching SD with an unsupported version
        self.validate_version(manifest.getVersion())
        self._lock = threading.Lock()

    # Life cycle

    def setup(self):
        """
        Called after storage domain is produced in the storage domain monitor.
        """

    def teardown(self):
        """
        Called after storage domain monitor finished and will never access the
        storage domain object.
        """

    @contextmanager
    def tearing_down(self):
        """
        Context manager which ensures that upon exiting context, storage domain
        is torn down.
        """
        try:
            yield
        finally:
            self.teardown()

    # Other

    @property
    def sdUUID(self):
        return self._manifest.sdUUID

    @property
    def alignment(self):
        return self._manifest.alignment

    @property
    def block_size(self):
        return self._manifest.block_size

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

    @property
    def manifest(self):
        return self._manifest

    def replaceMetadata(self, md):
        """
        Used by FormatConverter to replace the metadata reader/writer
        """
        self._manifest.replaceMetadata(md)

    def getMonitoringPath(self):
        return self._manifest.getMonitoringPath()

    def getVolumeSize(self, imgUUID, volUUID):
        """
        Return VolumeSize named tuple for specified volume.
        """
        return self._manifest.getVolumeSize(imgUUID, volUUID)

    @deprecated
    def getVSize(self, imgUUID, volUUID):
        """
        Return volume apparent size.

        Deprecated - use getVolumeSize().apparentsize instead.
        """
        return self._manifest.getVSize(imgUUID, volUUID)

    @deprecated
    def getVAllocSize(self, imgUUID, volUUID):
        """
        Return volume true size.

        Deprecated - use getVolumeSize().truesize instead.
        """
        return self._manifest.getVAllocSize(imgUUID, volUUID)

    def deleteImage(self, sdUUID, imgUUID, volsImgs):
        self._manifest.deleteImage(sdUUID, imgUUID, volsImgs)

    def purgeImage(self, sdUUID, imgUUID, volsImgs, discard):
        self._manifest.purgeImage(sdUUID, imgUUID, volsImgs, discard)

    def getAllImages(self):
        return self._manifest.getAllImages()

    def getAllVolumes(self):
        return self._manifest.getAllVolumes()

    def dump(self, full=False):
        return self._manifest.dump(full=full)

    def iter_volumes(self):
        """
        Iterate over all volumes.

        Yields:
            Volume instance
        """
        all_volumes = self.getAllVolumes()
        for vol_id, (img_ids, _) in six.iteritems(all_volumes):
            # The first img_id is the id of the template or the only image
            # where the volume id appears.
            img_id = img_ids[0]

            yield self.produceVolume(img_id, vol_id)

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
        return self._manifest.supportsSparseness

    @property
    def supports_inquire(self):
        return self._manifest.supports_inquire

    def recommends_unordered_writes(self, format):
        return self._manifest.recommends_unordered_writes(format)

    @property
    def oop(self):
        return self._manifest.oop

    def qcow2_compat(self):
        return self._manifest.qcow2_compat()

    def _makeClusterLock(self, domVersion=None):
        return self._manifest._makeDomainLock(domVersion)

    @classmethod
    def create(cls, sdUUID, domainName, domClass, typeSpecificArg, version,
               block_size=sc.BLOCK_SIZE_512, max_hosts=sc.HOSTS_4K_1M):
        """
        Create a storage domain. The initial status is unattached.
        The storage domain underlying storage must be visible (connected)
        at that point.
        """
        pass

    @classmethod
    def _validate_block_size(cls, block_size, version):
        """
        Validate that block size can be used with this storage domain class.
        """
        if version < 5:
            if block_size != sc.BLOCK_SIZE_512:
                raise se.InvalidParameterException('block_size', block_size)
        else:
            if block_size not in cls.supported_block_size:
                raise se.InvalidParameterException('block_size', block_size)

    @classmethod
    def _validate_storage_block_size(cls, block_size, storage_block_size):
        """
        Validate that block size matches storage block size, returning the
        block size that should be used with this storage.
        """
        # If we cannot detect the storage block size, use the user block size
        # or fallback to safe default.
        if storage_block_size == sc.BLOCK_SIZE_NONE:
            if block_size != sc.BLOCK_SIZE_AUTO:
                return block_size
            else:
                return sc.BLOCK_SIZE_512

        # If we can detect the storage block size and the user does not care
        # about it, use it.
        if block_size == sc.BLOCK_SIZE_AUTO:
            return storage_block_size

        # Otherwise verify that the user block size matches the storage block
        # size.
        if block_size == storage_block_size:
            return block_size

        raise se.StorageDomainBlockSizeMismatch(block_size, storage_block_size)

    @classmethod
    def validate_version(cls, version):
        if version not in cls.supported_versions:
            raise se.UnsupportedDomainVersion(version)

    def _registerResourceNamespaces(self):
        """
        Register resources namespaces and create
        factories for it.
        """
        # Register image resource namespace
        imageResourceFactory = \
            resourceFactories.ImageResourceFactory(self.sdUUID)
        imageResourcesNamespace = rm.getNamespace(sc.IMAGE_NAMESPACE,
                                                  self.sdUUID)
        try:
            rm.registerNamespace(imageResourcesNamespace, imageResourceFactory)
        except rm.NamespaceRegistered:
            self.log.debug("Resource namespace %s already registered",
                           imageResourcesNamespace)

        volumeResourcesNamespace = rm.getNamespace(sc.VOLUME_NAMESPACE,
                                                   self.sdUUID)
        try:
            rm.registerNamespace(volumeResourcesNamespace,
                                 rm.SimpleResourceFactory())
        except rm.NamespaceRegistered:
            self.log.debug("Resource namespace %s already registered",
                           volumeResourcesNamespace)

    def produceVolume(self, imgUUID, volUUID):
        """
        Produce a type specific Volume object
        """
        return self.getVolumeClass()(self.mountpoint, self.sdUUID, imgUUID,
                                     volUUID)

    def validateCreateVolumeParams(self, volFormat, srcVolUUID, diskType=None,
                                   preallocate=None, add_bitmaps=False):
        return self._manifest.validateCreateVolumeParams(
            volFormat, srcVolUUID, diskType=diskType, preallocate=preallocate,
            add_bitmaps=add_bitmaps)

    def createVolume(self, imgUUID, capacity, volFormat, preallocate, diskType,
                     volUUID, desc, srcImgUUID, srcVolUUID,
                     initial_size=None, add_bitmaps=False, legal=True):
        """
        Create a new volume
        """
        return self.getVolumeClass().create(
            self._getRepoPath(), self.sdUUID, imgUUID, capacity, volFormat,
            preallocate, diskType, volUUID, desc, srcImgUUID, srcVolUUID,
            initial_size=initial_size, add_bitmaps=add_bitmaps, legal=legal)

    def getMDPath(self):
        return self._manifest.getMDPath()

    def initSPMlease(self):
        return self._manifest.initDomainLock()

    def getVersion(self):
        return self._manifest.getVersion()

    def getFormat(self):
        return self._manifest.getFormat()

    def getPools(self):
        return self._manifest.getPools()

    def getIdsFilePath(self):
        return self._manifest.getIdsFilePath()

    def getLeasesFilePath(self):
        return self._manifest.getLeasesFilePath()

    def getReservedId(self):
        return self._manifest.getReservedId()

    def acquireHostId(self, hostId, wait=True):
        self._manifest.acquireHostId(hostId, wait)

    def releaseHostId(self, hostId, wait=True, unused=False):
        self._manifest.releaseHostId(hostId, wait, unused)

    def hasHostId(self, hostId):
        return self._manifest.hasHostId(hostId)

    def getHostStatus(self, hostId):
        return self._manifest.getHostStatus(hostId)

    def hasVolumeLeases(self):
        return self._manifest.hasVolumeLeases()

    def getVolumeLease(self, imgUUID, volUUID):
        return self._manifest.getVolumeLease(imgUUID, volUUID)

    def getClusterLease(self):
        return self._manifest.getDomainLease()

    def acquireClusterLock(self, hostID):
        self._manifest.acquireDomainLock(hostID)

    def releaseClusterLock(self):
        self._manifest.releaseDomainLock()

    def inspectClusterLock(self):
        return self._manifest.inspectDomainLock()

    def inquireClusterLock(self):
        return self._manifest.inquireDomainLock()

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
        VM_PATTERN = os.path.join(vmsPath, sc.UUID_GLOB_PATTERN)
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
        self.log.info("Creating vms dir: %s" % vmsDir)
        self.oop.fileUtils.createdir(vmsDir)
        tasksDir = self.getTasksDir()
        self.log.info("Creating task dir: %s" % tasksDir)
        self.oop.fileUtils.createdir(tasksDir)

    def activate(self):
        """
        Activate a storage domain that is already a member in a storage pool.
        """
        if self.isBackup():
            self.log.info("Storage Domain %s is of type backup, "
                          "adding master directory", self.sdUUID)
            self.mountMaster()
            self.createMasterTree()

    def _getRepoPath(self):
        return self._manifest.getRepoPath()

    def getImageDir(self, imgUUID):
        return self._manifest.getImageDir(imgUUID)

    getLinkBCImagePath = getImageDir

    def getImageRundir(self, imgUUID):
        return os.path.join(sc.P_VDSM_STORAGE, self.sdUUID, imgUUID)

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
        info['block_size'] = self.block_size
        info['alignment'] = self.alignment

        return info

    def getStats(self):
        """
        """
        pass

    def validateMasterMount(self):
        raise NotImplementedError

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

    def extendVolume(self, volumeUUID, size, refresh=True):
        pass

    def reduceVolume(self, imgUUID, volumeUUID, allowActive=False):
        pass

    @staticmethod
    def findDomainPath(sdUUID):
        raise NotImplementedError

    def getMetadata(self):
        return self._manifest.getMetadata()

    def setMetadata(self, newMetadata):
        with self._metadata.transaction():
            self._metadata.clear()
            self._metadata.update(newMetadata)

    def invalidateMetadata(self):
        self._metadata.invalidate()

    def getMetaParam(self, key):
        return self._manifest.getMetaParam(key)

    def getStorageType(self):
        return self._manifest.getStorageType()

    def getDomainRole(self):
        return self._manifest.getDomainRole()

    def getDomainClass(self):
        return self._manifest.getDomainClass()

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
        self._manifest.refreshDirTree()

    def refresh(self):
        self._manifest.refresh()

    def extend(self, devlist, force):
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
        return self._manifest.isISO()

    def isBackup(self):
        return self._manifest.isBackup()

    def isData(self):
        return self._manifest.isData()

    def imageGarbageCollector(self):
        """
        Image Garbage Collector
        remove the remnants of the removed images (they could be left sometimes
        (on NFS mostly) due to lazy file removal
        """
        pass

    def getVolumeClass(self):
        """
        Return a type specific volume generator object
        """
        raise NotImplementedError

    def supports_bitmaps_operations(self):
        return self._manifest.supports_bitmaps_operations()

    # External leases support

    @classmethod
    def supports_external_leases(cls, version):
        return cls.manifestClass.supports_external_leases(version)

    @classmethod
    def format_external_leases(
            cls, lockspace, path, alignment=sc.ALIGNMENT_1M,
            block_size=sc.BLOCK_SIZE_512):
        """
        Format the special xleases volume.

        Called when creating a new storage domain, or when upgrading storage
        domain to version 4.

        WARNING: destructive operation, must not be called on active external
        leases volume.

        TODO: should move to storage domain subclasses of each subclass can use
        its own backend.

        Must be called only on the SPM.
        """
        with cls.manifestClass.external_leases_backend(
                lockspace, path) as backend:
            xlease.format_index(
                lockspace,
                backend,
                alignment=alignment,
                block_size=block_size)

    @classmethod
    def is_block(cls):
        """
        Returns whether a Storage Domain is block-based
        """
        return False

    def external_leases_path(self):
        return self._manifest.external_leases_path()

    def create_external_leases(self):
        """
        Create the external leases special volume.

        Called during upgrade from version 3 to version 4.

        Must be called only on the SPM.
        """
        raise NotImplementedError

    def create_lease(self, lease_id, metadata=None, host_id=None):
        """
        Create an external lease on the external leases volume.

        Must be called only on the SPM.
        """
        with self._manifest.external_leases_lock.exclusive:
            with self._manifest.external_leases_volume() as vol:
                vol.add(lease_id)

        if metadata:
            metadata = validators.JobMetadata(metadata)

            self._manifest.acquire_external_lease(lease_id, host_id)

            try:
                now = int(time.time())
                job_metadata = {
                    "type": metadata.type,
                    "generation": metadata.generation,
                    "job_id": metadata.job_id,
                    "job_status": metadata.job_status,
                    "created": now,
                    "modified": now,
                    "host_hardware_id": host.uuid(),
                }

                self._manifest.set_lvb(lease_id, job_metadata)
            finally:
                self._manifest.release_external_lease(lease_id)

    def delete_lease(self, lease_id):
        """
        Delete an external lease on the external leases volume.

        Must be called only on the SPM.
        """
        with self._manifest.external_leases_lock.exclusive:
            with self._manifest.external_leases_volume() as vol:
                vol.remove(lease_id)

    def rebuild_external_leases(self):
        """
        Rebuild the external leases volume index from volume contents.

        Must be called only on the SPM.
        """
        with self._manifest.external_leases_lock.exclusive:
            path = self.external_leases_path()
            backend = xlease.DirectFile(path)
            with utils.closing(backend):
                xlease.rebuild_index(
                    self.sdUUID,
                    backend,
                    alignment=self._manifest.alignment,
                    block_size=self._manifest.block_size)

    def dump_external_leases(self):
        """
        Dump the external leases volume index contents.

        May be called on any host.
        """
        with self._manifest.external_leases_lock.shared:
            with self._manifest.external_leases_volume() as vol:
                return vol.dump()

    # Images

    def create_image(self, imgUUID):
        """
        Create placeholder for image's volumes
        """
        image_dir = self.getImageDir(imgUUID)
        if not os.path.isdir(image_dir):
            self.log.info("Create placeholder %s for image's volumes",
                          image_dir)
            task_name = "create image rollback: " + imgUUID
            recovery = task.Recovery(task_name, "sd", "StorageDomain",
                                     "create_image_rollback", [image_dir])
            vars.task.pushRecovery(recovery)
            os.mkdir(image_dir)
        return image_dir

    @classmethod
    def create_image_rollback(cls, task, image_dir):
        """
        Remove empty image folder
        """
        cls.log.info("create image rollback (image_dir=%s)", image_dir)
        if os.path.exists(image_dir):
            if not len(os.listdir(image_dir)):
                cls.log.info("Removing image directory %r", image_dir)
                fileUtils.cleanupdir(image_dir)
            else:
                cls.log.error("create image rollback: Cannot remove dirty "
                              "image (image_dir=%s)",
                              image_dir)

    # Format conversion

    def convert_volumes_metadata(self, target_version):
        """
        Add new keys for version target_version to volumes metadata. The
        operation must be completed by calling finalize_volumes_metadata().

        Must be called before domain metadata was converted.

        Must be implemented by concrete storge domains.
        """
        raise NotImplementedError

    def convert_metadata(self, target_version):
        """
        Convert domain metadata to version target_version.

        Must be called after convert_volumes_metadata().
        """
        current_version = self.getVersion()

        if not (current_version == 4 and target_version == 5):
            raise RuntimeError(
                "Cannot convert domain {} from version {} to version {}"
                .format(self.sdUUID, current_version, target_version))

        self.log.info(
            "Converting domain %s metadata from version %s to version %s",
            self.sdUUID, current_version, target_version)

        with self._metadata.transaction():
            self._metadata[DMDK_VERSION] = target_version

            # V4 domain never supported anything else, no need to probe
            # storage.
            self._metadata[DMDK_BLOCK_SIZE] = sc.BLOCK_SIZE_512
            self._metadata[DMDK_ALIGNMENT] = sc.ALIGNMENT_1M

            # Keys removed in v5, may exists in block storage domain.
            if DMDK_LOGBLKSIZE in self._metadata:
                del self._metadata[DMDK_LOGBLKSIZE]
            if DMDK_PHYBLKSIZE in self._metadata:
                del self._metadata[DMDK_PHYBLKSIZE]

    def finalize_volumes_metadata(self, target_version):
        """
        Rewrite volumes metadata, removing older keys kept during
        convert_volumes_metadata().

        Must be called after domain version was converted.

        Must be implemented by concrete storge domains.
        """
        raise NotImplementedError

    # Dumping storage domain

    def dump_lockspace(self):
        """
        Dump lockspace records.
        """
        return list(sanlock_direct.dump_lockspace(
            self.getIdsFilePath(),
            size=self.alignment,
            block_size=self.block_size,
            alignment=self.alignment))


class ExternalLease(guarded.AbstractLock):

    def __init__(self, host_id, sd_id, lease_id):
        self._host_id = host_id
        self._sd_id = sd_id
        self._lease_id = lease_id

    @property
    def ns(self):
        return rm.getNamespace(sc.EXTERNAL_LEASE_NAMESPACE, self._lease_id)

    @property
    def name(self):
        return self._lease_id

    @property
    def mode(self):
        return rm.EXCLUSIVE

    def acquire(self):
        dom = sdCache.produce_manifest(self._sd_id)
        dom.acquire_external_lease(self._lease_id, self._host_id)

    def release(self):
        dom = sdCache.produce_manifest(self._sd_id)
        dom.release_external_lease(self._lease_id)
