#
# Copyright 2009-2016 Red Hat, Inc.
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

########################################################
#
#  Set of user defined exceptions.
#
########################################################

#######################################################
#
# IMPORTANT NOTE: DO NOT USE CODES GREATER THAN 5000
# AS THEY ARE ASSIGNED TO THE OVIRT ENGINE !!!!!!!!!
#
#######################################################

from __future__ import absolute_import

from vdsm.common.exception import GeneralException
from vdsm.storage.securable import SecureError
SPM_STATUS_ERROR = (654, "Not SPM")

GENERAL_EXCEPTION = lambda e: (100, str(e))
ERROR_MAP = {
    SecureError: SPM_STATUS_ERROR
}


def generateResponse(error, default=GENERAL_EXCEPTION):
    resp = ERROR_MAP.get(type(error), default)
    if callable(resp):
        resp = resp(error)

    code, msg = resp

    return {'status': {'code': code, 'message': msg}}


#################################################
# Validation Exceptions
#################################################

class InvalidParameterException(GeneralException):
    code = 1000
    msg = "Invalid parameter"

    def __init__(self, name, value, reason=None):
        if reason is None:
            self.value = "%s=%s" % (name, value)
        else:
            self.value = "%s=%s (%s)" % (name, value, reason)


class InvalidDefaultExceptionException(GeneralException):
    code = 1001
    msg = "Cannot set exception as default, type not supported"


#################################################
# General Storage Exceptions
#################################################

class StorageException(GeneralException):
    code = 200
    msg = "General Storage Exception"


class ResourceException(GeneralException):
    code = 3000
    msg = "Resource operation failed"

    def __init__(self, UUID):
        self.value = "UUID={}".format(UUID)


class VolumeGeneralException(GeneralException):
    code = 4000
    msg = "Volume exception"

    def __init__(self, volume, *args):
        try:
            sdUUID = "sdUUID: %s" % volume.sdUUID
            imgUUID = "imgUUID: %s" % volume.imgUUID
            volUUID = "volUUID: %s" % volume.volUUID
            self.value = [sdUUID, imgUUID, volUUID]
        except:
            self.value = [repr(volume)]

        if len(args):
            self.value += list(args)


class UnicodeArgumentException(GeneralException):
    code = 4900
    msg = "Unicode arguments are not supported"


#################################################
# Misc Exceptions
#################################################

class MiscNotImplementedException(GeneralException):
    code = 2000
    msg = "Method not implemented"


class MiscFileReadException(StorageException):
    code = 2001
    msg = "Internal file read failure"


class MiscFileWriteException(StorageException):
    code = 2002
    msg = "Internal file write failure"


class MiscBlockReadException(StorageException):
    def __init__(self, name, offset, size):
        self.value = "name=%s, offset=%s, size=%s" % (name, offset, size)
    code = 2003
    msg = "Internal block device read failure"


class MiscBlockWriteException(StorageException):
    def __init__(self, name, offset, size):
        self.value = "name=%s, offset=%s, size=%s" % (name, offset, size)
    code = 2004
    msg = "Internal block device write failure"


class MiscOperationInProgress(StorageException):
    code = 2005
    msg = "Operation is already in progress"


class MiscBlockWriteIncomplete(MiscBlockWriteException):
    code = 2006
    msg = "Internal block device write incomplete"


class MiscBlockReadIncomplete(MiscBlockReadException):
    code = 2007
    msg = "Internal block device read incomplete"


class MiscDirCleanupFailure(StorageException):
    code = 2008
    msg = "Directory cleanup failure"


class UnsupportedOperation(StorageException):
    code = 2009
    msg = "Unsupported operation"
    expected = True

    def __init__(self, reason, **context):
        self.value = "reason={}, context={}".format(reason, context)


#################################################
#  Volumes Exceptions
#################################################

class VolumeDoesNotExist(StorageException):
    code = 201
    msg = "Volume does not exist"


class IncorrectFormat(StorageException):
    code = 202
    msg = "Incorrect Volume format"


class VolumeIsBusy(VolumeGeneralException):
    code = 203
    msg = "Volume is busy"


class VolumeImageHasChildren(VolumeGeneralException):
    code = 204
    msg = "Cannot delete volume which has children (non-ethical)"


class VolumeCreationError(StorageException):
    code = 205
    msg = "Error creating a new volume"


class VolumeExtendingError(StorageException):
    code = 206
    msg = "Error extending volume"


class VolumeMetadataReadError(StorageException):
    code = 207
    msg = "Error while processing volume meta data"


class VolumeMetadataWriteError(StorageException):
    code = 208
    msg = "Error while updating volume meta data"


class VolumeAccessError(StorageException):
    code = 209
    msg = "Error accessing a volume"


class VolumeUnlinkError(StorageException):
    code = 210
    msg = "Volume unlink failed"


class OrphanVolumeError(StorageException):
    code = 211
    msg = "Orphan volume, volume hasn't image"


class VolumeAlreadyExists(StorageException):
    code = 212
    msg = "Volume already exists"


class VolumeNonWritable(VolumeGeneralException):
    code = 213
    msg = "Volume cannot be access to writes"


class VolumeNonShareable(VolumeGeneralException):
    code = 214
    msg = "Volume cannot be shared, it's not Shared/Template volume"


class VolumeOwnershipError(VolumeGeneralException):
    code = 215
    msg = "Volume ownership error"


class VolumeCannotGetParent(StorageException):
    code = 216
    msg = "Cannot get parent volume"


class CannotCloneVolume(VolumeGeneralException):
    def __init__(self, src, dst, msg):
        self.value = "src=%s, dst=%s: %s" % (src, dst, msg)
    code = 217
    msg = "Cannot clone volume"


class CannotShareVolume(VolumeGeneralException):
    def __init__(self, src, dst, msg):
        self.value = "src=%s, dst=%s: %s" % (src, dst, msg)
    code = 218
    msg = "Cannot share volume"


class SharedVolumeNonWritable(VolumeGeneralException):
    code = 219
    msg = "Shared volume is read only"


class InternalVolumeNonWritable(VolumeGeneralException):
    code = 220
    msg = "Volume cannot be access to writes, it's Internal volume"


class CannotModifyVolumeTime(VolumeGeneralException):
    code = 221
    msg = "Cannot change volume's modify time"


class CannotDeleteVolume(StorageException):
    code = 222
    msg = "Volume deletion error"


class CannotDeleteSharedVolume(StorageException):
    code = 223
    msg = "Shared Volume cannot be deleted"


class NonLeafVolumeNotWritable(VolumeGeneralException):
    code = 224
    msg = "Volume cannot be accessed to writes, it's not a leaf volume"


class VolumeCopyError(VolumeGeneralException):
    code = 225
    msg = "Volume copy failed"


class createIllegalVolumeSnapshotError(StorageException):
    code = 226
    msg = "Cannot create volume snapshot from illegal volume"


class prepareIllegalVolumeError(StorageException):
    code = 227
    msg = "Cannot prepare illegal volume"


# class createVolumeRollbackError(StorageException):
#     code = 228
#     msg = "Failure create volume rollback"


class createVolumeSizeError(StorageException):
    code = 229
    msg = "Requested size is too small. Must be larger or equal to 1"


class VolumeWasNotPreparedBeforeTeardown(StorageException):
    code = 230
    msg = "Volume was not prepared before being torn down"


class IncorrectType(StorageException):
    code = 231
    msg = "Incorrect Volume Preallocate Type"


class VolumeResizeValueError(StorageException):
    code = 232
    msg = "Incorrect size value for volume resize"


# class VolumeNotSparse(StorageException):
#    code = 233
#    msg = "Volume type is not sparse"


# class CannotSparsifyVolume(StorageException):
#    code = 234
#    msg = "Cannot sparsify volume"


class InvalidVolumeUpdate(StorageException):
    code = 235
    msg = "Cannot update volume attributes"

    def __init__(self, vol_id, reason):
        self.value = "vol_id=%s, reason=%s" % (vol_id, reason)


#################################################
#  Images Exceptions
#################################################

class ImagesActionError(StorageException):
    code = 250
    msg = "Error images action"


class TemplateCreationError(StorageException):
    code = 251
    msg = "Error creating template from VM"


class MergeSnapshotsError(StorageException):
    code = 252
    msg = "Error merging snapshots"


class MoveImageError(StorageException):
    code = 253
    msg = "Error moving image"


class ImagePathError(StorageException):
    code = 254
    msg = "Image path does not exist or cannot be accessed/created"


class ImageValidationError(StorageException):
    code = 255
    msg = "Image validation error"


class ImageDeleteError(StorageException):
    code = 256
    msg = "Could not remove all image's volumes"


# REMOVED in 2.3.
# This class was not in use in 2.2 nor in 2.3.
# class ImageIsNotEmpty(StorageException):
#    def __init__(self, image, list):
#        self.value = "image=%s, files=%s" % (image, list)
#    code = 257
#    msg = "Image is not empty"


class ImageIsEmpty_deprecated_vdsm23(StorageException):
    def __init__(self, imgUUID, sdUUID):
        self.value = "image=%s, domain=%s" % (imgUUID, sdUUID)
    code = 258
    msg = "Image is empty. Deprecated in vdsm2.3"


class SourceImageActionError(StorageException):
    def __init__(self, imgUUID, sdUUID, msg=""):
        self.value = "image=%s, source domain=%s: %s" % (imgUUID, sdUUID, msg)
    code = 259
    msg = "Error during source image manipulation"


class DestImageActionError(StorageException):
    def __init__(self, imgUUID, sdUUID, msg=""):
        self.value = ("image=%s, dest domain=%s: "
                      "msg=%s" % (imgUUID, sdUUID, msg))
    code = 260
    msg = "Error during destination image manipulation"


class CopyImageError(StorageException):
    code = 261
    msg = "low level Image copy failed"


class ImageIsNotLegalChain(StorageException):
    code = 262
    msg = "Image is not a legal chain"


class CouldNotValideTemplateOnTargetDomain(StorageException):
    code = 263
    msg = "Cannot validate template on target domain"


class MultipleMoveImageError(StorageException):
    code = 264
    msg = "Error moving multiple image"


class OverwriteImageError(StorageException):
    def __init__(self, imgUUID, sdUUID):
        self.value = "image=%s, domain=%s" % (imgUUID, sdUUID)
    code = 265
    msg = "Can't overwrite image"


class MoveTemplateImageError(StorageException):
    code = 266
    msg = "Cannot move template's image because it is used by a VM"


# class MergeVolumeRollbackError(StorageException):
#    code = 267
#    msg = "Cannot rollback merge volume"


class ImageDoesNotExistInSD(StorageException):
    code = 268
    msg = "Image does not exist in domain"

    def __init__(
            self, imgUUID, sdUUID, tmpImgUUID=None, tmpVolUUID=None):
        self.value = "image=%s, domain=%s" % (imgUUID, sdUUID)
        self.tmpImgUUID = tmpImgUUID
        self.tmpVolUUID = tmpVolUUID


#################################################
#  Pool Exceptions
#################################################

class StoragePoolActionError(StorageException):
    code = 300
    msg = "Error storage pool action"


class StoragePoolCreationError(StorageException):
    code = 301
    msg = "Error creating a storage pool"


class StoragePoolConnectionError(StorageException):
    code = 302
    msg = "Error storage pool connection"


class StoragePoolDisconnectionError(StorageException):
    code = 303
    msg = "Error storage pool disconnection"


class StoragePoolMasterNotFound(StorageException):
    def __init__(self, spUUID, msdUUID=None):
        self.value = "spUUID=%s, msdUUID=%s" % (spUUID, msdUUID)
    code = 304
    msg = "Cannot find master domain"


class StorageUpdateVmError(StorageException):
    code = 305
    msg = "Cannot update VM"


class ReconstructMasterError(StorageException):
    code = 306
    msg = "Cannot reconstruct master domain"


class StoragePoolTooManyMasters(StorageException):
    code = 307
    msg = "Too many masters for StoragePool"


class StoragePoolDestroyingError(StorageException):
    code = 308
    msg = "Error destroying a storage pool"


class StoragePoolUnknown(StorageException):
    code = 309
    msg = "Unknown pool id, pool not connected"


class StoragePoolHasPotentialMaster(StorageException):
    code = 310
    msg = "Master role should be moved to another domain"


class StoragePoolInternalError(StorageException):
    code = 311
    msg = "Storage pool not defined"


class ImageMissingFromVm(StorageException):
    def __init__(self, imgUUID, vmUUID):
        self.value = "image=%s, VM=%s" % (imgUUID, vmUUID)
    code = 312
    msg = "Image missing from VM"


class StoragePoolNotConnected(StorageException):
    code = 313
    msg = "Storage pool not connected"


# Code 314 was used for GetIsoListError, removed in 4.18
# Code 315 was used for GetFloppyListError, removed in 4.18


class StoragePoolAlreadyExists(StorageException):
    code = 316
    msg = "Error creating a storage pool - pool already exists"


class IsoCannotBeMasterDomain(StorageException):
    code = 317
    msg = "ISO domain cannot be a master storage domain"


class StoragePoolCheckError(StorageException):
    code = 318
    msg = "Pool check failed"


class BackupCannotBeMasterDomain(StorageException):
    code = 319
    msg = "Backup domain cannot be a master storage domain"


class MissingOvfFileFromVM(StorageException):
    code = 320
    msg = "Missing OVF file from VM"


class ImageNotOnTargetDomain(StorageException):
    def __init__(self, imgUUID, vmUUID, sdUUID):
        self.value = "SD=%s, image=%s, VM=%s" % (sdUUID, imgUUID, vmUUID)
    code = 321
    msg = "Image cannot be found on the specified domain"


class VMPathNotExists(StorageException):
    code = 322
    msg = "Cannot find VMs directory"


class CannotConnectMultiplePools(StorageException):
    code = 323
    msg = "Cannot connect pool, already connected to another pool"


class StoragePoolWrongMaster(StorageException):
    def __init__(self, spUUID, sdUUID):
        self.value = "SD=%s, pool=%s" % (sdUUID, spUUID)
    code = 324
    msg = "Wrong Master domain or its version"


class StoragePoolConnected(StorageException):
    code = 325
    msg = "Cannot perform action while storage pool is connected"


class StoragePoolHigherVersionMasterFound(StorageException):
    code = 326
    msg = "Found master domain with higher master version than input"


class StoragePoolDescriptionTooLongError(StorageException):
    code = 327
    msg = "Storage pool description is too long"


class TooManyDomainsInStoragePoolError(StorageException):
    code = 328
    msg = "Too many domains in Storage pool"


class ImagesNotSupportedError(StorageException):
    code = 329
    msg = "This domain does not support images"


class GetFileStatsError(StorageException):
    code = 330
    msg = "Cannot get file stats"


#################################################
#  Domains Exceptions
#################################################

class StorageDomainBlockSizeMismatch(StorageException):
    code = 348
    msg = "Block size does not match storage block size"

    def __init__(self, block_size, storage_block_size):
        self.value = "block_size=%s, storage_block_size=%s" % (
            block_size, storage_block_size)


class DiscardIsNotSupported(StorageException):
    code = 349
    msg = "Discard is not supported by storage domain"

    def __init__(self, sdUUID, reason):
        self.value = "sdUUID=%s, reason=%s" % (sdUUID, reason)


class StorageDomainActionError(StorageException):
    code = 350
    msg = "Error in storage domain action"


class StorageDomainCreationError(StorageException):
    code = 351
    msg = "Error creating a storage domain"


class StorageDomainFormatError(StorageException):
    code = 352
    msg = "Error formatting a storage domain"


class StorageDomainNotInPool(StorageException):
    def __init__(self, spUUID, sdUUID):
        self.value = "domain=%s, pool=%s" % (sdUUID, spUUID)
    code = 353
    msg = "Storage domain not in pool"


class StorageDomainAttachError(StorageException):
    code = 354
    msg = "Error attaching storage domain"


class StorageDomainMasterError(StorageException):
    code = 355
    msg = "Error validating master storage domain"


class StorageDomainDetachError(StorageException):
    code = 356
    msg = "Error detaching storage domain"


class StorageDomainDeactivateError(StorageException):
    code = 357
    msg = "Error deactivating storage domain"


class StorageDomainDoesNotExist(StorageException):
    code = 358
    msg = "Storage domain does not exist"


class StorageDomainActivateError(StorageException):
    code = 359
    msg = "Error activating storage domain"


class StorageDomainFSNotMounted(StorageException):
    code = 360
    msg = "Storage domain remote path not mounted"


class StorageDomainNotEmpty(StorageException):
    code = 361
    msg = "Storage domain is not empty - requires cleaning"


class StorageDomainMetadataCreationError(StorageException):
    code = 362
    msg = "Error creating a storage domain's metadata"


class StorageDomainMetadataFileMissing(ResourceException):
    code = 363
    msg = "Could not retrieve metadata file name for domain"


class StorageDomainMetadataNotFound(StorageException):
    def __init__(self, sdUUID, path):
        self.value = "sdUUID=%s, metafile path=%s" % (sdUUID, path)
    code = 364
    msg = "Storage domain invalid, metadata not found"


class StorageDomainAlreadyExists(StorageException):
    code = 365
    msg = "Storage domain already exists"


class StorageDomainMasterUnmountError(StorageException):
    def __init__(self, masterdir, rc):
        self.value = "masterdir=%s, rc=%s" % (masterdir, rc)
    code = 366
    msg = "Error unmounting master storage domain"


class BlockStorageDomainMasterFSCKError(StorageException):
    def __init__(self, masterfsdev, rc):
        self.value = "masterfsdev=%s, rc=%s" % (masterfsdev, rc)
    code = 367
    msg = "BlockSD master file system FSCK error"


class BlockStorageDomainMasterMountError(StorageException):
    code = 368
    msg = "BlockSD master file system mount error"

    def __init__(self, masterfsdev, rc, out, err):
        self.value = ("masterfsdev={}, rc={}, out={!r}, err={!r}"
                      .format(masterfsdev, rc, out, err))


class StorageDomainNotActive(StorageException):
    code = 369
    msg = "Storage domain not active"


class StorageDomainMasterCopyError(StorageException):
    code = 370
    msg = "Error copying master storage domain's data"


class StorageDomainLayoutError(StorageException):
    code = 371
    msg = "Storage domain layout corrupted"


class StorageDomainTypeError(StorageException):
    code = 372
    msg = "Unsupported Storage Domain type"


class GetStorageDomainListError(StorageException):
    code = 373
    msg = "Cannot get Storage Domains list"


class VolumesZeroingError(StorageException):
    code = 374
    msg = "Cannot zero out volume"


class StorageDomainNotMemberOfPool(StorageException):
    def __init__(self, spUUID, sdUUID):
        self.value = "pool=%s, domain=%s" % (spUUID, sdUUID)
    code = 375
    msg = "Domain is not member in pool"


class StorageDomainStatusError(StorageException):
    code = 376
    msg = "Unsupported Storage Domain status"


class StorageDomainCheckError(StorageException):
    code = 377
    msg = "Domain has errors"


class StorageDomainTypeNotBackup(StorageException):
    code = 378
    msg = "Domain type should be 'backup' but is not"


class StorageDomainAccessError(StorageException):
    code = 379
    msg = "Domain is either partially accessible or entirely inaccessible"


class StorageDomainAlreadyAttached(StorageException):
    def __init__(self, spUUID, sdUUID):
        self.value = "domain=%s, pool=%s" % (sdUUID, spUUID)
    code = 380
    msg = "Storage domain already attached to pool"


# DEPRECATED. Should be removed.
class StorageDomainStateTransitionIllegal(StorageException):
    def __init__(self, sdUUID, currState, nextState):
        self.value = [sdUUID, currState, nextState]
    code = 381
    msg = "Domain state change illegal"


class StorageDomainActive(StorageException):
    code = 382
    msg = "Illegal action, domain active"


class CannotDetachMasterStorageDomain(StorageException):
    code = 383
    msg = "Illegal action"


class FileStorageDomainStaleNFSHandle(StorageException):
    code = 384
    msg = "Stale NFS handle on underlying NFS server"


class StorageDomainInsufficientPermissions(StorageException):
    code = 385
    msg = "Insufficient access permissions to underlying storage"


class StorageDomainClassError(StorageException):
    code = 386
    msg = "Invalid domain class value"


class StorageDomainDescriptionTooLongError(StorageException):
    code = 387
    msg = "Storage domain description is too long"


# Removed Exception. Commented for code number reference.
# class StorageDomainIsMadeFromTooManyPVs(StorageException):
#     code = 388
#     msg = "Storage domain is made from too many PVs"


class TooManyPVsInVG(StorageException):
    code = 389
    msg = "Tried to create a VG from too many PVs"


class StorageDomainIllegalRemotePath(StorageException):
    code = 390
    msg = "Remote path is illegal"


class CannotFormatAttachedStorageDomain(StorageException):
    code = 391
    msg = "Cannot format attached storage domain"


class CannotFormatStorageDomainInConnectedPool(StorageException):
    code = 392
    msg = "Cannot format storage domain in connected pool"


class StorageDomainRefreshError(StorageException):
    code = 393
    msg = "Cannot refresh storage domain"


class UnsupportedDomainVersion(StorageException):
    def __init__(self, version="unspecified"):
        self.value = ""
        self.version = version
        self.msg = ("Domain version `%d` is unsupported "
                    "by this version of VDSM" % version)
    code = 394


class CurrentVersionTooAdvancedError(StorageException):
    def __init__(self, sdUUID, curVer, expVer):
        self.value = ""
        self.msg = ("Current domain `%s` version is too advanced, "
                    "expected `%d` and found `%d`" %
                    (sdUUID, expVer, curVer))
    code = 395


class PoolUpgradeInProgress(StorageException):
    def __init__(self, spUUID):
        self.value = ""
        self.msg = ("Upgrading a pool while an upgrade is in process is "
                    "unsupported (pool: `%s`)" % (spUUID,))
    code = 396


class NoSpaceLeftOnDomain(StorageException):
    def __init__(self, sdUUID):
        self.value = sdUUID
        self.msg = "No space left on domain %s" % (sdUUID,)
    code = 397


class MixedSDVersionError(StorageException):
    def __init__(self, sdUUID, domVersion, msdUUID, msdVersion):
        self.value = ""
        self.msg = ("Domain `%s` version (%d) is different from "
                    "msd %s version (%d)" %
                    (sdUUID, domVersion, msdUUID, msdVersion))
    code = 398


class StorageDomainTargetUnsupported(StorageException):
    code = 399
    msg = "Storage Domain target is unsupported"


#################################################
# Task Exceptions
#################################################

class InvalidTask(GeneralException):
    code = 400
    msg = "Task invalid"


class UnknownTask(GeneralException):
    code = 401
    msg = "Task id unknown"


class TaskClearError(GeneralException):
    code = 402
    msg = "Could not clear task"


class TaskNotFinished(GeneralException):
    code = 403
    msg = "Task not finished"


class InvalidTaskType(GeneralException):
    code = 404
    msg = "Invalid task type"


class AddTaskError(GeneralException):
    code = 405
    msg = "TaskManager error, unable to add task"


class TaskInProgress(GeneralException):
    code = 406
    msg = "Running Task in progress"

    def __init__(self, spUUID, task):
        self.value = "Pool %s task %s" % (spUUID, task)


class TaskMetaDataSaveError(GeneralException):
    code = 407
    msg = "Can't save Task Metadata"


class TaskMetaDataLoadError(GeneralException):
    code = 408
    msg = "Can't load Task Metadata"


class TaskDirError(GeneralException):
    code = 409
    msg = "can't find/access task dir"


class TaskStateError(GeneralException):
    code = 410
    msg = "Operation is not allowed in this task state"


class TaskAborted(GeneralException):
    code = 411
    msg = "Task is aborted"

    def __init__(self, value, abortedcode=code):
        self.value = "value={} abortedcode={}".format(value, abortedcode)
        self.abortedcode = abortedcode


class UnmanagedTask(GeneralException):
    code = 412
    msg = "Operation can't be performed on unmanaged task"


class TaskPersistError(GeneralException):
    code = 413
    msg = "Can't persist task"


class InvalidJob(GeneralException):
    code = 420
    msg = "Job is invalid"


class InvalidRecovery(GeneralException):
    code = 430
    msg = "Recovery is invalid"


class InvalidTaskMng(GeneralException):
    code = 440
    msg = "invalid Task Manager"


class TaskStateTransitionError(GeneralException):
    code = 441
    msg = "cannot move task to requested state"


class TaskHasRefs(GeneralException):
    code = 442
    msg = "operation cannot be performed - task has active references"


#################################################
#  Connections Exceptions
#################################################

class StorageServerActionError(StorageException):
    code = 450
    msg = "Error storage server action"


class StorageServerConnectionError(StorageException):
    code = 451
    msg = "Error storage server connection"


class StorageServerDisconnectionError(StorageException):
    code = 452
    msg = "Error storage server disconnection"


class StorageServerValidationError(StorageException):
    code = 453
    msg = "The specified path does not exist or cannot be reached."\
          " Verify the path is correct and for remote storage,"\
          " check the connection to your storage."

    def __init__(self, targetPath=''):
        self.value = "path = %s" % targetPath


class StorageServeriSCSIError(StorageException):
    code = 454
    msg = "iSCSI connection error"


class MultipathReloadError(StorageException):
    code = 455
    msg = "Multipath service reload error"


class GetiSCSISessionListError(StorageServeriSCSIError):
    code = 456
    msg = "Get iSCSI session list error"


class AddiSCSIPortalError(StorageServeriSCSIError):
    code = 457
    msg = "Add iSCSI portal error"


class RemoveiSCSIPortalError(StorageServeriSCSIError):
    code = 458
    msg = "Remove iSCSI portal error"


class RemoveiSCSINodeError(StorageServeriSCSIError):
    code = 459
    msg = "Remove iSCSI node error"


class AddiSCSINodeError(StorageServeriSCSIError):
    code = 460
    msg = "Add iSCSI node error"


class SetiSCSIAuthError(StorageServeriSCSIError):
    code = 461
    msg = "Set iSCSI authentication error"


class SetiSCSIUsernameError(StorageServeriSCSIError):
    code = 462
    msg = "Set iSCSI username error"


class SetiSCSIPasswdError(StorageServeriSCSIError):
    code = 463
    msg = "Set iSCSI password error"


class iSCSILoginError(StorageServeriSCSIError):
    code = 464
    msg = "Failed to login to iSCSI node"


class iSCSISetupError(StorageServeriSCSIError):
    code = 465
    msg = "Failed to setup iSCSI subsystem"


# class DeviceNotFound(StorageException):
#    code = 466
#    msg = "Device not found or not accessible"


class MultipathSetupError(StorageException):
    code = 467
    msg = "Failed to setup multipath"


class StorageTypeError(StorageException):
    code = 468
    msg = "Storage type error"


class StorageServerAccessPermissionError(StorageException):
    code = 469
    msg = "Permission settings on the specified path do not allow"\
          " access to the storage. Verify permission settings"\
          " on the specified storage path."

    def __init__(self, targetPath):
        self.value = "path = %s" % targetPath


class MountTypeError(StorageException):
    code = 470
    msg = "Mount type error"


class MountParsingError(StorageException):
    code = 471
    msg = "Mount parsing error"


class InvalidIpAddress(StorageException):
    code = 472
    msg = "Invalid IP address"

    def __init__(self, ip):
        self.value = "IP = %s" % (ip)


class iSCSIifaceError(StorageServeriSCSIError):
    code = 473
    msg = "iscsiadm iface error"


class iSCSILogoutError(StorageServeriSCSIError):
    code = 474
    msg = "Failed to logout from iSCSI node"


class iSCSIDiscoveryError(StorageServeriSCSIError):
    code = 475
    msg = "Failed discovery of iSCSI targets"

    def __init__(self, portal, err):
        self.value = "portal=%s, err=%s" % (portal, err)


class iSCSILoginAuthError(StorageServeriSCSIError):
    code = 476
    msg = "Failed to login to iSCSI node due to authorization failure"


class MountError(StorageException):
    code = 477
    msg = "Problem while trying to mount target"


class StorageServerConnectionRefIdAlreadyInUse(StorageException):
    code = 478
    msg = "Connection Reference ID is already in use"


class StorageServerConnectionRefIdDoesNotExist(StorageException):
    code = 479
    msg = "Connection Reference ID was not registered"


class UnsupportedGlusterVolumeReplicaCountError(StorageException):
    code = 480
    msg = "Gluster volume replica count is not supported"

    def __init__(self, replicaCount):
        self.value = "replica count = %s" % replicaCount


class ImageTicketsError(StorageException):
    code = 481
    msg = "Cannot communicate with image daemon"

    def __init__(self, reason):
        self.value = "reason=%s" % reason


class ImageDaemonError(StorageException):
    code = 482
    msg = "Image daemon request failed"

    def __init__(self, status, reason, error_info):
        self.value = "status={}, reason={}, error={}".format(
            status, reason, error_info)


class ImageDaemonUnsupported(StorageException):
    code = 483
    msg = "Image daemon is unsupported"


class ImageVerificationError(StorageException):
    code = 484
    msg = "Image verification failed"

    def __init__(self, reason):
        self.value = "reason=%s" % reason


#################################################
#  LVM related Exceptions
#################################################

class VolumeGroupActionError(StorageException):
    code = 500
    msg = "Error volume group action"


class VolumeGroupPermissionsError(StorageException):
    code = 501
    msg = "Could not update/change volume group permissions"


class VolumeGroupCreateError(StorageException):
    def __init__(self, vgname, devname):
        self.value = "vgname=%s, devname=%s" % (vgname, devname)
    code = 502
    msg = "Cannot create Volume Group"


class VolumeGroupExtendError(StorageException):
    def __init__(self, vgname, devname):
        self.value = "vgname=%s, devname=%s" % (vgname, devname)
    code = 503
    msg = "Cannot extend Volume Group"


class VolumeGroupSizeError(StorageException):
    code = 504
    msg = "Volume Group not big enough"


class VolumeGroupAlreadyExistsError(StorageException):
    code = 505
    msg = "Volume Group Already Exists"


class VolumeGroupDoesNotExist(StorageException):
    code = 506
    msg = "Volume Group does not exist"


class VolumeGroupRenameError(StorageException):
    code = 507
    msg = "Volume Group rename error"


class VolumeGroupRemoveError(StorageException):
    code = 508
    msg = "Volume Group remove error"


class VolumeGroupUninitialized(StorageException):
    code = 509
    msg = "Volume Group not initialize"


class VolumeGroupReadTagError(StorageException):
    code = 510
    msg = "Read Volume Group's tag error"


class VolumeGroupScanError(StorageException):
    code = 513
    msg = "Volume Group scanning error"


class GetVolumeGroupListError(StorageException):
    code = 514
    msg = "Get Volume Group list error"


class VolumeGroupHasDomainTag(StorageException):
    code = 515
    msg = "Volume Group has domain tag - requires cleaning"


class VolumeGroupReplaceTagError(StorageException):
    code = 516
    msg = "Replace Volume Group tag error"


class VolumeGroupBlockSizeError(StorageException):
    def __init__(self, domsizes, devsizes):
        self.value = "domlogblksize=%s domphyblksize=%s " \
                     "devlogblksize=%s devphyblksize=%s" % (
                         domsizes[0], domsizes[1],
                         devsizes[0], devsizes[1])
    code = 517
    msg = "All devices in domain must have the same block size"


class DeviceBlockSizeError(StorageException):
    def __init__(self, devsizes):
        self.value = "logblksize=%s phyblksize=%s" % \
                     (devsizes[0], devsizes[1])
    code = 518
    msg = "Device block size is not supported"


class VolumeGroupReduceError(StorageException):
    code = 519
    msg = "Cannot reduce the Volume Group"

    def __init__(self, vgname, pvname, err):
        self.value = "vgname=%s pvname=%s err=%s" % (vgname, pvname, err)


class CannotCreateLogicalVolume(StorageException):
    code = 550
    msg = "Cannot create Logical Volume"

    def __init__(self, vgname, lvname, err):
        self.value = "vgname=%s lvname=%s err=%s" % (vgname, lvname, err)


class CannotRemoveLogicalVolume(StorageException):
    code = 551
    msg = "Cannot remove Logical Volume"


class CannotDeactivateLogicalVolume(StorageException):
    code = 552
    msg = "Cannot deactivate Logical Volume"


class CannotAccessLogicalVolume(StorageException):
    code = 553
    msg = "Cannot access Logical Volume"


class LogicalVolumeExtendError(StorageException):
    def __init__(self, vgname, lvname, newsize):
        self.value = ("vgname=%s lvname=%s "
                      "newsize=%s" % (vgname, lvname, newsize))
    code = 554
    msg = "Logical Volume extend failed"


class LogicalVolumesListError(StorageException):
    code = 555
    msg = "Cannot get Logical Volumes list from Volume Group"


class LogicalVolumeRefreshError(StorageException):
    code = 556
    msg = "Cannot refresh Logical Volume"


class LogicalVolumeScanError(StorageException):
    def __init__(self, vgname, lvname):
        self.value = "vgname=%s lvname=%s" % (vgname, lvname)
    code = 557
    msg = "Logical volume scanning error"


class CannotActivateLogicalVolume(StorageException):
    code = 558
    msg = "Cannot activate Logical Volume"


class LogicalVolumePermissionsError(ResourceException):
    code = 559
    msg = "Cannot update/change logical volume permissions"


class LogicalVolumeAddTagError(StorageException):
    code = 560
    msg = "Add tag to Logical Volume error"


class LogicalVolumeRemoveTagError(StorageException):
    code = 561
    msg = "Remove tag from Logical Volume error"


class GetLogicalVolumeTagError(StorageException):
    code = 562
    msg = "Cannot get tags of Logical Volumes"


class GetLogicalVolumesByTagError(StorageException):
    code = 563
    msg = "Cannot get Logical Volumes with specific tag"


class GetAllLogicalVolumeTagsError(StorageException):
    code = 564
    msg = "Cannot get tags of all Logical Volumes of Volume Group"


class GetLogicalVolumeDevError(StorageException):
    code = 565
    msg = "Cannot get physical devices of logical volume"


class LogicalVolumeRenameError(StorageException):
    code = 566
    msg = "Cannot rename Logical Volume"


class CannotWriteAccessLogialVolume(StorageException):
    def __init__(self, vgname, lvname):
        self.value = "vgname=%s lvname=%s" % (vgname, lvname)
    code = 567
    msg = "Cannot access logical volume for write"


class CannotSetRWLogicalVolume(StorageException):
    def __init__(self, vgname, lvname, rw):
        self.value = "vgname=%s lvname=%s rw=%s" % (vgname, lvname, rw)
    code = 568
    msg = "Cannot set Logical volume RW permission"


class LogicalVolumesScanError(StorageException):
    def __init__(self, vgname, lvs):
        self.value = "vgname=%s, lvs=%s" % (vgname, lvs)
    code = 569
    msg = "Logical volume scanning error"


class CannotActivateLogicalVolumes(StorageException):
    code = 570
    msg = "Cannot activate Logical Volumes"


class GetLogicalVolumeDataError(StorageException):
    code = 571
    msg = "Cannot get Logical Volume Info"


class LogicalVolumeReplaceTagError(StorageException):
    code = 572
    msg = "Replace Logical Volume tag error"


class BlockDeviceActionError(StorageException):
    code = 600
    msg = "Error block device action"


class PhysDevInitializationError(StorageException):
    code = 601
    msg = "Failed to initialize physical device"


class LVMSetupError(StorageException):
    code = 602
    msg = "LVM setup failed"


class CouldNotRetrievePhysicalVolumeList(StorageException):
    code = 603
    msg = "Could not retrieve pv list"


class LogicalVolumeAlreadyExists(StorageException):
    code = 604
    msg = "Cannot create logical volume - already exists"


class CouldNotRetrieveLogicalVolumesList(StorageException):
    code = 605
    msg = "Could not retrieve lv list"


class InaccessiblePhysDev(StorageException):
    def __init__(self, devices):
        self.value = "devices=%s" % (devices,)
    code = 606
    msg = "Multipath cannot access physical device(s)"


class PartitionedPhysDev(StorageException):
    code = 607
    msg = "Partitioned physical device"


class MkfsError(StorageException):
    code = 608
    msg = "Cannot create filesystem on device"


class MissingTagOnLogicalVolume(StorageException):
    def __init__(self, lvname, tag):
        self.value = "lvname=%s tag=%s" % (lvname, tag)
    code = 609
    msg = "Missing logical volume tag."


class LogicalVolumeDoesNotExistError(StorageException):
    code = 610
    msg = "Logical volume does not exist"


class LogicalVolumeCachingError(StorageException):
    code = 611
    msg = "Logical volume cache error"


class LogicalVolumeWrongTagError(StorageException):
    code = 612
    msg = "Wrong logical volume tag"


class VgMetadataCriticallyFull(StorageException):
    def __init__(self, vgname, mdasize, mdafree):
        self.value = ("vgname=%s mdasize=%s "
                      "mdafree=%s" % (vgname, mdasize, mdafree))
    code = 613
    msg = (
        "Error - The system has reached the high watermark on the VG "
        "metadata area size. This is due high number of Vdisks or "
        "large Vdisks size allocated on this specific VG. Please call "
        "Support to address the issue")


class SmallVgMetadata(StorageException):
    def __init__(self, vgname, mdasize, mdafree):
        self.value = ("vgname=%s mdasize=%s "
                      "mdafree=%s" % (vgname, mdasize, mdafree))
    code = 614
    msg = (
        "Warning - The allocated VG metadata area size is too small, "
        "which might limit its capacity (the number of Vdisks and/or "
        "their size). Refer to GSS knowledge base to understand the "
        "issue and how to resolve it")


class CouldNotResizePhysicalVolume(StorageException):
    def __init__(self, pvname, err):
        self.value = "pvname=%s err=%s" % (pvname, err)
    code = 615
    msg = "Could not resize PV"


class UnexpectedVolumeGroupMetadata(StorageException):
    def __init__(self, reason):
        self.value = "reason=%s" % reason
    code = 616
    msg = "Volume Group metadata isn't as expected"


class ForbiddenPhysicalVolumeOperation(StorageException):
    code = 617
    msg = "The operation couldn't be performed on the provided pv"

    def __init__(self, reason):
        self.value = "reason=%s" % reason


class CouldNotMovePVData(StorageException):
    code = 618
    msg = "Could not move PV data, there might be leftovers that require" \
          " manual handling - please refer to the pvmove man page"

    def __init__(self, pvname, vgname, err):
        self.value = "pvname=%s vgname=%s err=%s" % (pvname, vgname, err)


class NoSuchPhysicalVolume(StorageException):
    code = 619
    msg = "No such PV"

    def __init__(self, pvname, vgname):
        self.value = "pvname=%s vgname=%s" % (pvname, vgname)


class NoSuchDestinationPhysicalVolumes(StorageException):
    code = 620
    msg = "No such destination PVs"

    def __init__(self, pvs, vgname):
        self.value = "pvs=%s vgname=%s" % (pvs, vgname)


#################################################
#  SPM/HSM Exceptions
#################################################

class SpmStartError(StorageException):
    code = 650
    msg = "Error starting SPM"


class AcquireLockFailure(StorageException):
    def __init__(self, id, rc, out, err):
        self.value = "id=%s, rc=%s, out=%s, err=%s" % (id, rc, out, err)
    code = 651
    msg = "Cannot obtain lock"


class SpmParamsMismatch(StorageException):
    def __init__(self, oldlver, oldid, prevLVER, prevID):
        self.value = ("expected previd:%s lver:%s "
                      "got request for previd:%s lver:%s" %
                      (oldid, oldlver, prevID, prevLVER))
    code = 652
    msg = "Pool previous lver/id don't match request"


class SpmStopError(StorageException):
    def __init__(self, spUUID, strRunningTask=None):
        self.value = "spUUID=%s, task=%s" % (spUUID, strRunningTask)
    code = 653
    msg = "Error stopping SPM, SPM has unfinished task(s)"


class SpmStatusError(StorageException):
    code = 654
    msg = "Not SPM"


# Removed Exception. Commented for code number reference.
# class SpmFenceError(StorageException):
#     code = 655
#     msg = "Error fencing SPM"


class IsSpm(StorageException):
    code = 656
    msg = "Operation not allowed while SPM is active"


class DomainAlreadyLocked(StorageException):
    code = 657
    msg = "Cannot acquire lock, resource marked as locked"


class DomainLockDoesNotExist(StorageException):
    code = 658
    msg = "Cannot release lock, resource not found"


# Removed Exception. Commented for code number reference.
# class CannotRetrieveSpmStatus(StorageException):
#     code = 659
#     msg = ("Cannot retrieve SPM status, master domain probably "
#                "unavailable")


class ReleaseLockFailure(StorageException):
    code = 660
    msg = "Cannot release lock"


class AcquireHostIdFailure(StorageException):
    code = 661
    msg = "Cannot acquire host id"


class ReleaseHostIdFailure(StorageException):
    code = 662
    msg = "Cannot release host id"


class HostIdMismatch(StorageException):
    code = 700
    msg = "Host id not found or does not match manager host id"


class ClusterLockInitError(StorageException):
    code = 701
    msg = "Could not initialize cluster lock"


class InquireNotSupportedError(StorageException):
    code = 702
    msg = "Cluster lock inquire isnt supported"


#################################################
#  Meta data related Exceptions
#################################################

class MetaDataGeneralError(StorageException):
    code = 749
    msg = "General Meta data error"


class MetaDataKeyError(MetaDataGeneralError):
    code = 750
    msg = "Meta data key error"


class MetaDataKeyNotFoundError(MetaDataGeneralError):
    code = 751
    msg = "Meta Data key not found error"


class MetaDataSealIsBroken(MetaDataGeneralError):
    def __init__(self, cksum, computed_cksum):
        self.value = ("cksum = %s, "
                      "computed_cksum = %s" % (cksum, computed_cksum))
    code = 752
    msg = "Meta Data seal is broken (checksum mismatch)"


class MetaDataValidationError(MetaDataGeneralError):
    code = 753
    msg = "Meta Data self-validation failed"


class MetaDataMappingError(MetaDataGeneralError):
    code = 754
    msg = "Meta Data mapping failed"


# Removed Exception. Commented for code number reference.
# class MetaDataParamError(MetaDataGeneralError):
#     code = 755
#     msg = "Meta Data parameter invalid"


class MetadataOverflowError(MetaDataGeneralError):
    code = 756
    msg = "Metadata is too big. Cannot change Metadata"

    def __init__(self, data):
        self.value = "data=%r" % data


class MetadataCleared(MetaDataKeyNotFoundError):
    code = 757
    msg = "Metadata was cleared, volume is partly deleted"


#################################################
#  Import/Export Exceptions
#################################################

class ImportError(StorageException):
    code = 800
    msg = "Error importing image"


class ImportInfoError(StorageException):
    code = 801
    msg = "Import candidate info error"


class ImportUnknownType(StorageException):
    code = 802
    msg = "Unknown import type"


class ExportError(StorageException):
    code = 803
    msg = "Error exporting VM"


#################################################
#  Resource Exceptions
#################################################

class ResourceNamespaceNotEmpty(GeneralException):
    code = 850
    msg = "Resource Namespace is not empty"


class ResourceTimeout(GeneralException):
    code = 851
    msg = "Resource timeout"


class ResourceDoesNotExist(GeneralException):
    code = 852
    msg = "Resource does not exist"


class InvalidResourceName(GeneralException):
    code = 853
    msg = "Invalid resource name"


class ResourceReferenceInvalid(GeneralException):
    code = 854
    msg = ("Cannot perform operation. This resource has been released or "
           "expired.")


class ResourceAcqusitionFailed(GeneralException):
    code = 855
    msg = ("Could not acquire resource. "
           "Probably resource factory threw an exception.")


#################################################
#  External domains exceptions
#  Range: 900-909
#################################################

class StorageDomainIsMemberOfPool(StorageException):
    code = 900
    msg = "Storage domain is member of pool"

    def __init__(self, sdUUID):
        self.value = "domain=%s" % (sdUUID,)


#################################################
#  SDM Errors
#  Range: 910-919
#################################################

class DomainHasGarbage(StorageException):
    code = 910
    msg = "Operation failed because garbage was found"

    def __init__(self, reason):
        self.value = reason


class GenerationMismatch(StorageException):
    code = 911
    msg = "The provided generation does not match the actual generation"

    def __init__(self, requested, actual):
        self.value = "requested=%s, actual=%s" % (requested, actual)


class VolumeIsNotInChain(StorageException):
    code = 920
    msg = "Volume is not part of the chain."

    def __init__(self, sd_id, img_id, vol_id):
        self.value = ("sd_id=%s, img_id=%s, vol_id=%s" %
                      (vol_id, sd_id, img_id))


class WrongParentVolume(StorageException):
    code = 921
    msg = "Wrong parent volume."

    def __init__(self, vol_id, parent_id):
        self.value = "vol_id=%s, parent_id=%s" % (vol_id, parent_id)


class UnexpectedVolumeState(StorageException):
    code = 922
    msg = "Unexpected volume state."

    def __init__(self, base_vol_id, expected, actual):
        self.value = ("vol_id=%s, expected=%s, actual=%s" % (
                      base_vol_id, expected, actual))


#################################################
#  Managed Volume Errors
#  Range: 925-935
#################################################


class ManagedVolumeNotSupported(StorageException):
    code = 925
    msg = ("Managed Volume Not Supported. "
           "Missing package os-brick.")


class ManagedVolumeHelperFailed(StorageException):
    code = 926
    msg = "Managed Volume Helper failed."


class ManagedVolumeAlreadyAttached(StorageException):
    code = 927
    msg = "Managed Volume is already attached."

    def __init__(self, vol_id, path, attachment):
        self.value = "vol_id=%s path=%s attachment=%s" % (
            vol_id, path, attachment)


class ManagedVolumeUnsupportedDevice(StorageException):
    code = 928
    msg = "Unsupported Device: missing multipath_id"

    def __init__(self, vol_id, attachment):
        self.value = "vol_id=%s attachment=%s" % (vol_id, attachment)


class ManagedVolumeConnectionMismatch(StorageException):
    code = 929
    msg = "Attach existing volume with different connection information"

    def __init__(self, vol_id, expected, actual):
        self.value = "vol_id=%s expected=%s actual=%s" % (
            vol_id, expected, actual)


#################################################
#  VM leases Errors
#  Range: 936-940
#################################################


class NoSuchLease(StorageException):
    code = 936
    msg = "No such lease"
    expected = True

    def __init__(self, lease_id):
        self.value = "lease={}".format(lease_id)
