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
    message = "Invalid parameter"

    def __init__(self, name, value, reason=None):
        if reason is None:
            self.value = "%s=%s" % (name, value)
        else:
            self.value = "%s=%s (%s)" % (name, value, reason)


class InvalidDefaultExceptionException(GeneralException):
    code = 1001
    message = "Cannot set exception as default, type not supported"


#################################################
# General Storage Exceptions
#################################################

class StorageException(GeneralException):
    code = 200
    message = "General Storage Exception"


class ResourceException(GeneralException):
    code = 3000
    message = "Resource operation failed"

    def __init__(self, UUID):
        self.value = "UUID={}".format(UUID)


class VolumeGeneralException(GeneralException):
    code = 4000
    message = "Volume exception"

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
    message = "Unicode arguments are not supported"


#################################################
# Misc Exceptions
#################################################

class MiscNotImplementedException(GeneralException):
    code = 2000
    message = "Method not implemented"


class MiscFileReadException(StorageException):
    code = 2001
    message = "Internal file read failure"


class MiscFileWriteException(StorageException):
    code = 2002
    message = "Internal file write failure"


class MiscBlockReadException(StorageException):
    def __init__(self, name, offset, size):
        self.value = "name=%s, offset=%s, size=%s" % (name, offset, size)
    code = 2003
    message = "Internal block device read failure"


class MiscBlockWriteException(StorageException):
    def __init__(self, name, offset, size):
        self.value = "name=%s, offset=%s, size=%s" % (name, offset, size)
    code = 2004
    message = "Internal block device write failure"


class MiscOperationInProgress(StorageException):
    code = 2005
    message = "Operation is already in progress"


class MiscBlockWriteIncomplete(MiscBlockWriteException):
    code = 2006
    message = "Internal block device write incomplete"


class MiscBlockReadIncomplete(MiscBlockReadException):
    code = 2007
    message = "Internal block device read incomplete"


class MiscDirCleanupFailure(StorageException):
    code = 2008
    message = "Directory cleanup failure"


class UnsupportedOperation(StorageException):
    code = 2009
    message = "Unsupported operation"
    expected = True

    def __init__(self, reason, **context):
        self.value = "reason={}, context={}".format(reason, context)


#################################################
#  Volumes Exceptions
#################################################

class VolumeDoesNotExist(StorageException):
    code = 201
    message = "Volume does not exist"


class IncorrectFormat(StorageException):
    code = 202
    message = "Incorrect Volume format"


class VolumeIsBusy(VolumeGeneralException):
    code = 203
    message = "Volume is busy"


class VolumeImageHasChildren(VolumeGeneralException):
    code = 204
    message = "Cannot delete volume which has children (non-ethical)"


class VolumeCreationError(StorageException):
    code = 205
    message = "Error creating a new volume"


class VolumeExtendingError(StorageException):
    code = 206
    message = "Error extending volume"


class VolumeMetadataReadError(StorageException):
    code = 207
    message = "Error while processing volume meta data"


class VolumeMetadataWriteError(StorageException):
    code = 208
    message = "Error while updating volume meta data"


class VolumeAccessError(StorageException):
    code = 209
    message = "Error accessing a volume"


class VolumeUnlinkError(StorageException):
    code = 210
    message = "Volume unlink failed"


class OrphanVolumeError(StorageException):
    code = 211
    message = "Orphan volume, volume hasn't image"


class VolumeAlreadyExists(StorageException):
    code = 212
    message = "Volume already exists"


class VolumeNonWritable(VolumeGeneralException):
    code = 213
    message = "Volume cannot be access to writes"


class VolumeNonShareable(VolumeGeneralException):
    code = 214
    message = "Volume cannot be shared, it's not Shared/Template volume"


class VolumeOwnershipError(VolumeGeneralException):
    code = 215
    message = "Volume ownership error"


class VolumeCannotGetParent(StorageException):
    code = 216
    message = "Cannot get parent volume"


class CannotCloneVolume(VolumeGeneralException):
    def __init__(self, src, dst, msg):
        self.value = "src=%s, dst=%s: %s" % (src, dst, msg)
    code = 217
    message = "Cannot clone volume"


class CannotShareVolume(VolumeGeneralException):
    def __init__(self, src, dst, msg):
        self.value = "src=%s, dst=%s: %s" % (src, dst, msg)
    code = 218
    message = "Cannot share volume"


class SharedVolumeNonWritable(VolumeGeneralException):
    code = 219
    message = "Shared volume is read only"


class InternalVolumeNonWritable(VolumeGeneralException):
    code = 220
    message = "Volume cannot be access to writes, it's Internal volume"


class CannotModifyVolumeTime(VolumeGeneralException):
    code = 221
    message = "Cannot change volume's modify time"


class CannotDeleteVolume(StorageException):
    code = 222
    message = "Volume deletion error"


class CannotDeleteSharedVolume(StorageException):
    code = 223
    message = "Shared Volume cannot be deleted"


class NonLeafVolumeNotWritable(VolumeGeneralException):
    code = 224
    message = "Volume cannot be accessed to writes, it's not a leaf volume"


class VolumeCopyError(VolumeGeneralException):
    code = 225
    message = "Volume copy failed"


class createIllegalVolumeSnapshotError(StorageException):
    code = 226
    message = "Cannot create volume snapshot from illegal volume"


class prepareIllegalVolumeError(StorageException):
    code = 227
    message = "Cannot prepare illegal volume"


# class createVolumeRollbackError(StorageException):
#     code = 228
#     message = "Failure create volume rollback"


class createVolumeSizeError(StorageException):
    code = 229
    message = "Requested size is too small. Must be larger or equal to 1"


class VolumeWasNotPreparedBeforeTeardown(StorageException):
    code = 230
    message = "Volume was not prepared before being torn down"


class IncorrectType(StorageException):
    code = 231
    message = "Incorrect Volume Preallocate Type"


class VolumeResizeValueError(StorageException):
    code = 232
    message = "Incorrect size value for volume resize"


class VolumeNotSparse(StorageException):
    code = 233
    message = "Volume type is not sparse"


class CannotSparsifyVolume(StorageException):
    code = 234
    message = "Cannot sparsify volume"


class InvalidVolumeUpdate(StorageException):
    code = 235
    message = "Cannot update volume attributes"

    def __init__(self, vol_id, reason):
        self.value = "vol_id=%s, reason=%s" % (vol_id, reason)


#################################################
#  Images Exceptions
#################################################

class ImagesActionError(StorageException):
    code = 250
    message = "Error images action"


class TemplateCreationError(StorageException):
    code = 251
    message = "Error creating template from VM"


class MergeSnapshotsError(StorageException):
    code = 252
    message = "Error merging snapshots"


class MoveImageError(StorageException):
    code = 253
    message = "Error moving image"


class ImagePathError(StorageException):
    code = 254
    message = "Image path does not exist or cannot be accessed/created"


class ImageValidationError(StorageException):
    code = 255
    message = "Image validation error"


class ImageDeleteError(StorageException):
    code = 256
    message = "Could not remove all image's volumes"


# REMOVED in 2.3.
# This class was not in use in 2.2 nor in 2.3.
# class ImageIsNotEmpty(StorageException):
#    def __init__(self, image, list):
#        self.value = "image=%s, files=%s" % (image, list)
#    code = 257
#    message = "Image is not empty"


class ImageIsEmpty_deprecated_vdsm23(StorageException):
    def __init__(self, imgUUID, sdUUID):
        self.value = "image=%s, domain=%s" % (imgUUID, sdUUID)
    code = 258
    message = "Image is empty. Deprecated in vdsm2.3"


class SourceImageActionError(StorageException):
    def __init__(self, imgUUID, sdUUID, msg=""):
        self.value = "image=%s, source domain=%s: %s" % (imgUUID, sdUUID, msg)
    code = 259
    message = "Error during source image manipulation"


class DestImageActionError(StorageException):
    def __init__(self, imgUUID, sdUUID, msg=""):
        self.value = ("image=%s, dest domain=%s: "
                      "msg=%s" % (imgUUID, sdUUID, msg))
    code = 260
    message = "Error during destination image manipulation"


class CopyImageError(StorageException):
    code = 261
    message = "low level Image copy failed"


class ImageIsNotLegalChain(StorageException):
    code = 262
    message = "Image is not a legal chain"


class CouldNotValideTemplateOnTargetDomain(StorageException):
    code = 263
    message = "Cannot validate template on target domain"


class MultipleMoveImageError(StorageException):
    code = 264
    message = "Error moving multiple image"


class OverwriteImageError(StorageException):
    def __init__(self, imgUUID, sdUUID):
        self.value = "image=%s, domain=%s" % (imgUUID, sdUUID)
    code = 265
    message = "Can't overwrite image"


class MoveTemplateImageError(StorageException):
    code = 266
    message = "Cannot move template's image because it is used by a VM"


# class MergeVolumeRollbackError(StorageException):
#    code = 267
#    message = "Cannot rollback merge volume"


class ImageDoesNotExistInSD(StorageException):
    code = 268
    message = "Image does not exist in domain"

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
    message = "Error storage pool action"


class StoragePoolCreationError(StorageException):
    code = 301
    message = "Error creating a storage pool"


class StoragePoolConnectionError(StorageException):
    code = 302
    message = "Error storage pool connection"


class StoragePoolDisconnectionError(StorageException):
    code = 303
    message = "Error storage pool disconnection"


class StoragePoolMasterNotFound(StorageException):
    def __init__(self, spUUID, msdUUID=None):
        self.value = "spUUID=%s, msdUUID=%s" % (spUUID, msdUUID)
    code = 304
    message = "Cannot find master domain"


class StorageUpdateVmError(StorageException):
    code = 305
    message = "Cannot update VM"


class ReconstructMasterError(StorageException):
    code = 306
    message = "Cannot reconstruct master domain"


class StoragePoolTooManyMasters(StorageException):
    code = 307
    message = "Too many masters for StoragePool"


class StoragePoolDestroyingError(StorageException):
    code = 308
    message = "Error destroying a storage pool"


class StoragePoolUnknown(StorageException):
    code = 309
    message = "Unknown pool id, pool not connected"


class StoragePoolHasPotentialMaster(StorageException):
    code = 310
    message = "Master role should be moved to another domain"


class StoragePoolInternalError(StorageException):
    code = 311
    message = "Storage pool not defined"


class ImageMissingFromVm(StorageException):
    def __init__(self, imgUUID, vmUUID):
        self.value = "image=%s, VM=%s" % (imgUUID, vmUUID)
    code = 312
    message = "Image missing from VM"


class StoragePoolNotConnected(StorageException):
    code = 313
    message = "Storage pool not connected"


# Code 314 was used for GetIsoListError, removed in 4.18
# Code 315 was used for GetFloppyListError, removed in 4.18


class StoragePoolAlreadyExists(StorageException):
    code = 316
    message = "Error creating a storage pool - pool already exists"


class IsoCannotBeMasterDomain(StorageException):
    code = 317
    message = "ISO domain cannot be a master storage domain"


class StoragePoolCheckError(StorageException):
    code = 318
    message = "Pool check failed"


class BackupCannotBeMasterDomain(StorageException):
    code = 319
    message = "Backup domain cannot be a master storage domain"


class MissingOvfFileFromVM(StorageException):
    code = 320
    message = "Missing OVF file from VM"


class ImageNotOnTargetDomain(StorageException):
    def __init__(self, imgUUID, vmUUID, sdUUID):
        self.value = "SD=%s, image=%s, VM=%s" % (sdUUID, imgUUID, vmUUID)
    code = 321
    message = "Image cannot be found on the specified domain"


class VMPathNotExists(StorageException):
    code = 322
    message = "Cannot find VMs directory"


class CannotConnectMultiplePools(StorageException):
    code = 323
    message = "Cannot connect pool, already connected to another pool"


class StoragePoolWrongMaster(StorageException):
    def __init__(self, spUUID, sdUUID):
        self.value = "SD=%s, pool=%s" % (sdUUID, spUUID)
    code = 324
    message = "Wrong Master domain or its version"


class StoragePoolConnected(StorageException):
    code = 325
    message = "Cannot perform action while storage pool is connected"


class StoragePoolHigherVersionMasterFound(StorageException):
    code = 326
    message = "Found master domain with higher master version than input"


class StoragePoolDescriptionTooLongError(StorageException):
    code = 327
    message = "Storage pool description is too long"


class TooManyDomainsInStoragePoolError(StorageException):
    code = 328
    message = "Too many domains in Storage pool"


class ImagesNotSupportedError(StorageException):
    code = 329
    message = "This domain does not support images"


class GetFileStatsError(StorageException):
    code = 330
    message = "Cannot get file stats"


#################################################
#  Domains Exceptions
#################################################

class StorageDomainBlockSizeMismatch(StorageException):
    code = 348
    message = "Block size does not match storage block size"

    def __init__(self, block_size, storage_block_size):
        self.value = "block_size=%s, storage_block_size=%s" % (
            block_size, storage_block_size)


class DiscardIsNotSupported(StorageException):
    code = 349
    message = "Discard is not supported by storage domain"

    def __init__(self, sdUUID, reason):
        self.value = "sdUUID=%s, reason=%s" % (sdUUID, reason)


class StorageDomainActionError(StorageException):
    code = 350
    message = "Error in storage domain action"


class StorageDomainCreationError(StorageException):
    code = 351
    message = "Error creating a storage domain"


class StorageDomainFormatError(StorageException):
    code = 352
    message = "Error formatting a storage domain"


class StorageDomainNotInPool(StorageException):
    def __init__(self, spUUID, sdUUID):
        self.value = "domain=%s, pool=%s" % (sdUUID, spUUID)
    code = 353
    message = "Storage domain not in pool"


class StorageDomainAttachError(StorageException):
    code = 354
    message = "Error attaching storage domain"


class StorageDomainMasterError(StorageException):
    code = 355
    message = "Error validating master storage domain"


class StorageDomainDetachError(StorageException):
    code = 356
    message = "Error detaching storage domain"


class StorageDomainDeactivateError(StorageException):
    code = 357
    message = "Error deactivating storage domain"


class StorageDomainDoesNotExist(StorageException):
    code = 358
    message = "Storage domain does not exist"


class StorageDomainActivateError(StorageException):
    code = 359
    message = "Error activating storage domain"


class StorageDomainFSNotMounted(StorageException):
    code = 360
    message = "Storage domain remote path not mounted"


class StorageDomainNotEmpty(StorageException):
    code = 361
    message = "Storage domain is not empty - requires cleaning"


class StorageDomainMetadataCreationError(StorageException):
    code = 362
    message = "Error creating a storage domain's metadata"


class StorageDomainMetadataFileMissing(ResourceException):
    code = 363
    message = "Could not retrieve metadata file name for domain"


class StorageDomainMetadataNotFound(StorageException):
    def __init__(self, sdUUID, path):
        self.value = "sdUUID=%s, metafile path=%s" % (sdUUID, path)
    code = 364
    message = "Storage domain invalid, metadata not found"


class StorageDomainAlreadyExists(StorageException):
    code = 365
    message = "Storage domain already exists"


class StorageDomainMasterUnmountError(StorageException):
    def __init__(self, masterdir, rc):
        self.value = "masterdir=%s, rc=%s" % (masterdir, rc)
    code = 366
    message = "Error unmounting master storage domain"


class BlockStorageDomainMasterFSCKError(StorageException):
    def __init__(self, masterfsdev, rc):
        self.value = "masterfsdev=%s, rc=%s" % (masterfsdev, rc)
    code = 367
    message = "BlockSD master file system FSCK error"


class BlockStorageDomainMasterMountError(StorageException):
    code = 368
    message = "BlockSD master file system mount error"

    def __init__(self, masterfsdev, rc, out, err):
        self.value = ("masterfsdev={}, rc={}, out={!r}, err={!r}"
                      .format(masterfsdev, rc, out, err))


class StorageDomainNotActive(StorageException):
    code = 369
    message = "Storage domain not active"


class StorageDomainMasterCopyError(StorageException):
    code = 370
    message = "Error copying master storage domain's data"


class StorageDomainLayoutError(StorageException):
    code = 371
    message = "Storage domain layout corrupted"


class StorageDomainTypeError(StorageException):
    code = 372
    message = "Unsupported Storage Domain type"


class GetStorageDomainListError(StorageException):
    code = 373
    message = "Cannot get Storage Domains list"


class VolumesZeroingError(StorageException):
    code = 374
    message = "Cannot zero out volume"


class StorageDomainNotMemberOfPool(StorageException):
    def __init__(self, spUUID, sdUUID):
        self.value = "pool=%s, domain=%s" % (spUUID, sdUUID)
    code = 375
    message = "Domain is not member in pool"


class StorageDomainStatusError(StorageException):
    code = 376
    message = "Unsupported Storage Domain status"


class StorageDomainCheckError(StorageException):
    code = 377
    message = "Domain has errors"


class StorageDomainTypeNotBackup(StorageException):
    code = 378
    message = "Domain type should be 'backup' but is not"


class StorageDomainAccessError(StorageException):
    code = 379
    message = "Domain is either partially accessible or entirely inaccessible"


class StorageDomainAlreadyAttached(StorageException):
    def __init__(self, spUUID, sdUUID):
        self.value = "domain=%s, pool=%s" % (sdUUID, spUUID)
    code = 380
    message = "Storage domain already attached to pool"


# DEPRECATED. Should be removed.
class StorageDomainStateTransitionIllegal(StorageException):
    def __init__(self, sdUUID, currState, nextState):
        self.value = [sdUUID, currState, nextState]
    code = 381
    message = "Domain state change illegal"


class StorageDomainActive(StorageException):
    code = 382
    message = "Illegal action, domain active"


class CannotDetachMasterStorageDomain(StorageException):
    code = 383
    message = "Illegal action"


class FileStorageDomainStaleNFSHandle(StorageException):
    code = 384
    message = "Stale NFS handle on underlying NFS server"


class StorageDomainInsufficientPermissions(StorageException):
    code = 385
    message = "Insufficient access permissions to underlying storage"


class StorageDomainClassError(StorageException):
    code = 386
    message = "Invalid domain class value"


class StorageDomainDescriptionTooLongError(StorageException):
    code = 387
    message = "Storage domain description is too long"

# Removed Exception. Commented for code number reference.
# class StorageDomainIsMadeFromTooManyPVs(StorageException):
#     code = 388


class TooManyPVsInVG(StorageException):
    code = 389
    message = "Tried to create a VG from too many PVs"


class StorageDomainIllegalRemotePath(StorageException):
    code = 390
    message = "Remote path is illegal"


class CannotFormatAttachedStorageDomain(StorageException):
    code = 391
    message = "Cannot format attached storage domain"


class CannotFormatStorageDomainInConnectedPool(StorageException):
    code = 392
    message = "Cannot format storage domain in connected pool"


class StorageDomainRefreshError(StorageException):
    code = 393
    message = "Cannot refresh storage domain"


class UnsupportedDomainVersion(StorageException):
    def __init__(self, version="unspecified"):
        self.value = ""
        self.version = version
        self.message = ("Domain version %r is unsupported "
                        "by this version of VDSM" % version)
    code = 394


class CurrentVersionTooAdvancedError(StorageException):
    def __init__(self, sdUUID, curVer, expVer):
        self.value = ""
        self.message = ("Current domain `%s` version is too advanced, "
                        "expected `%d` and found `%d`" %
                        (sdUUID, expVer, curVer))
    code = 395


class PoolUpgradeInProgress(StorageException):
    def __init__(self, spUUID):
        self.value = ""
        self.message = ("Upgrading a pool while an upgrade is in process is "
                        "unsupported (pool: `%s`)" % (spUUID,))
    code = 396


class NoSpaceLeftOnDomain(StorageException):
    def __init__(self, sdUUID):
        self.value = sdUUID
        self.message = "No space left on domain %s" % (sdUUID,)
    code = 397


class MixedSDVersionError(StorageException):
    def __init__(self, sdUUID, domVersion, msdUUID, msdVersion):
        self.value = ""
        self.message = ("Domain `%s` version (%d) is different from "
                        "msd %s version (%d)" %
                        (sdUUID, domVersion, msdUUID, msdVersion))
    code = 398


class StorageDomainTargetUnsupported(StorageException):
    code = 399
    message = "Storage Domain target is unsupported"


#################################################
# Task Exceptions
#################################################

class InvalidTask(GeneralException):
    code = 400
    message = "Task invalid"


class UnknownTask(GeneralException):
    code = 401
    message = "Task id unknown"


class TaskClearError(GeneralException):
    code = 402
    message = "Could not clear task"


class TaskNotFinished(GeneralException):
    code = 403
    message = "Task not finished"


class InvalidTaskType(GeneralException):
    code = 404
    message = "Invalid task type"


class AddTaskError(GeneralException):
    code = 405
    message = "TaskManager error, unable to add task"


class TaskInProgress(GeneralException):
    code = 406
    message = "Running Task in progress"

    def __init__(self, spUUID, task):
        self.value = "Pool %s task %s" % (spUUID, task)


class TaskMetaDataSaveError(GeneralException):
    code = 407
    message = "Can't save Task Metadata"


class TaskMetaDataLoadError(GeneralException):
    code = 408
    message = "Can't load Task Metadata"


class TaskDirError(GeneralException):
    code = 409
    message = "can't find/access task dir"


class TaskStateError(GeneralException):
    code = 410
    message = "Operation is not allowed in this task state"


class TaskAborted(GeneralException):
    code = 411
    message = "Task is aborted"

    def __init__(self, value, abortedcode=code):
        self.value = "value={} abortedcode={}".format(value, abortedcode)
        self.abortedcode = abortedcode


class UnmanagedTask(GeneralException):
    code = 412
    message = "Operation can't be performed on unmanaged task"


class TaskPersistError(GeneralException):
    code = 413
    message = "Can't persist task"


class InvalidJob(GeneralException):
    code = 420
    message = "Job is invalid"


class InvalidRecovery(GeneralException):
    code = 430
    message = "Recovery is invalid"


class InvalidTaskMng(GeneralException):
    code = 440
    message = "invalid Task Manager"


class TaskStateTransitionError(GeneralException):
    code = 441
    message = "cannot move task to requested state"


class TaskHasRefs(GeneralException):
    code = 442
    message = "operation cannot be performed - task has active references"


#################################################
#  Connections Exceptions
#################################################

class StorageServerActionError(StorageException):
    code = 450
    message = "Error storage server action"


class StorageServerConnectionError(StorageException):
    code = 451
    message = "Error storage server connection"


class StorageServerDisconnectionError(StorageException):
    code = 452
    message = "Error storage server disconnection"


class StorageServerValidationError(StorageException):
    code = 453
    message = "The specified path does not exist or cannot be reached."\
              " Verify the path is correct and for remote storage,"\
              " check the connection to your storage."

    def __init__(self, targetPath=''):
        self.value = "path = %s" % targetPath


class StorageServeriSCSIError(StorageException):
    code = 454
    message = "iSCSI connection error"


class MultipathReloadError(StorageException):
    code = 455
    message = "Multipath service reload error"


class GetiSCSISessionListError(StorageServeriSCSIError):
    code = 456
    message = "Get iSCSI session list error"


class AddiSCSIPortalError(StorageServeriSCSIError):
    code = 457
    message = "Add iSCSI portal error"


class RemoveiSCSIPortalError(StorageServeriSCSIError):
    code = 458
    message = "Remove iSCSI portal error"


class RemoveiSCSINodeError(StorageServeriSCSIError):
    code = 459
    message = "Remove iSCSI node error"


class AddiSCSINodeError(StorageServeriSCSIError):
    code = 460
    message = "Add iSCSI node error"


class SetiSCSIAuthError(StorageServeriSCSIError):
    code = 461
    message = "Set iSCSI authentication error"


class SetiSCSIUsernameError(StorageServeriSCSIError):
    code = 462
    message = "Set iSCSI username error"


class SetiSCSIPasswdError(StorageServeriSCSIError):
    code = 463
    message = "Set iSCSI password error"


class iSCSILoginError(StorageServeriSCSIError):
    code = 464
    message = "Failed to login to iSCSI node"


class iSCSISetupError(StorageServeriSCSIError):
    code = 465
    message = "Failed to setup iSCSI subsystem"


# class DeviceNotFound(StorageException):
#    code = 466
#    message = "Device not found or not accessible"


class MultipathSetupError(StorageException):
    code = 467
    message = "Failed to setup multipath"


class StorageTypeError(StorageException):
    code = 468
    message = "Storage type error"


class StorageServerAccessPermissionError(StorageException):
    code = 469
    message = "Permission settings on the specified path do not allow"\
              " access to the storage. Verify permission settings"\
              " on the specified storage path."

    def __init__(self, targetPath):
        self.value = "path = %s" % targetPath


class MountTypeError(StorageException):
    code = 470
    message = "Mount type error"


class MountParsingError(StorageException):
    code = 471
    message = "Mount parsing error"


class InvalidIpAddress(StorageException):
    code = 472
    message = "Invalid IP address"

    def __init__(self, ip):
        self.value = "IP = %s" % (ip)


class iSCSIifaceError(StorageServeriSCSIError):
    code = 473
    message = "iscsiadm iface error"


class iSCSILogoutError(StorageServeriSCSIError):
    code = 474
    message = "Failed to logout from iSCSI node"


class iSCSIDiscoveryError(StorageServeriSCSIError):
    code = 475
    message = "Failed discovery of iSCSI targets"

    def __init__(self, portal, err):
        self.value = "portal=%s, err=%s" % (portal, err)


class iSCSILoginAuthError(StorageServeriSCSIError):
    code = 476
    message = "Failed to login to iSCSI node due to authorization failure"


class MountError(StorageException):
    code = 477
    message = "Problem while trying to mount target"


class StorageServerConnectionRefIdAlreadyInUse(StorageException):
    code = 478
    message = "Connection Reference ID is already in use"


class StorageServerConnectionRefIdDoesNotExist(StorageException):
    code = 479
    message = "Connection Reference ID was not registered"


class UnsupportedGlusterVolumeReplicaCountError(StorageException):
    code = 480
    message = "Gluster volume replica count is not supported"

    def __init__(self, replicaCount):
        self.value = "replica count = %s" % replicaCount


class ImageTicketsError(StorageException):
    code = 481
    message = "Cannot communicate with image daemon"

    def __init__(self, reason):
        self.value = "reason=%s" % reason


class ImageDaemonError(StorageException):
    code = 482
    message = "Image daemon request failed"

    def __init__(self, status, reason, error_info):
        self.value = "status={}, reason={}, error={}".format(
            status, reason, error_info)


class ImageDaemonUnsupported(StorageException):
    code = 483
    message = "Image daemon is unsupported"


class ImageVerificationError(StorageException):
    code = 484
    message = "Image verification failed"

    def __init__(self, reason):
        self.value = "reason=%s" % reason


#################################################
#  LVM related Exceptions
#################################################

class VolumeGroupActionError(StorageException):
    code = 500
    message = "Error volume group action"


class VolumeGroupPermissionsError(StorageException):
    code = 501
    message = "Could not update/change volume group permissions"


class VolumeGroupCreateError(StorageException):
    def __init__(self, vgname, devname):
        self.value = "vgname=%s, devname=%s" % (vgname, devname)
    code = 502
    message = "Cannot create Volume Group"


class VolumeGroupExtendError(StorageException):
    def __init__(self, vgname, devname):
        self.value = "vgname=%s, devname=%s" % (vgname, devname)
    code = 503
    message = "Cannot extend Volume Group"


class VolumeGroupSizeError(StorageException):
    code = 504
    message = "Volume Group not big enough"


class VolumeGroupAlreadyExistsError(StorageException):
    code = 505
    message = "Volume Group Already Exists"


class VolumeGroupDoesNotExist(StorageException):
    code = 506
    message = "Volume Group does not exist"


class VolumeGroupRenameError(StorageException):
    code = 507
    message = "Volume Group rename error"


class VolumeGroupRemoveError(StorageException):
    code = 508
    message = "Volume Group remove error"


class VolumeGroupUninitialized(StorageException):
    code = 509
    message = "Volume Group not initialize"


class VolumeGroupReadTagError(StorageException):
    code = 510
    message = "Read Volume Group's tag error"


class VolumeGroupScanError(StorageException):
    code = 513
    message = "Volume Group scanning error"


class GetVolumeGroupListError(StorageException):
    code = 514
    message = "Get Volume Group list error"


class VolumeGroupHasDomainTag(StorageException):
    code = 515
    message = "Volume Group has domain tag - requires cleaning"


class VolumeGroupReplaceTagError(StorageException):
    code = 516
    message = "Replace Volume Group tag error"


class VolumeGroupBlockSizeError(StorageException):
    def __init__(self, domsizes, devsizes):
        self.value = "domlogblksize=%s domphyblksize=%s " \
                     "devlogblksize=%s devphyblksize=%s" % (
                         domsizes[0], domsizes[1],
                         devsizes[0], devsizes[1])
    code = 517
    message = "All devices in domain must have the same block size"


class DeviceBlockSizeError(StorageException):
    def __init__(self, devsizes):
        self.value = "logblksize=%s phyblksize=%s" % \
                     (devsizes[0], devsizes[1])
    code = 518
    message = "Device block size is not supported"


class VolumeGroupReduceError(StorageException):
    code = 519
    message = "Cannot reduce the Volume Group"

    def __init__(self, vgname, pvname, err):
        self.value = "vgname=%s pvname=%s err=%s" % (vgname, pvname, err)


class CannotCreateLogicalVolume(StorageException):
    code = 550
    message = "Cannot create Logical Volume"

    def __init__(self, vgname, lvname, err):
        self.value = "vgname=%s lvname=%s err=%s" % (vgname, lvname, err)


class CannotRemoveLogicalVolume(StorageException):
    code = 551
    message = "Cannot remove Logical Volume"


class CannotDeactivateLogicalVolume(StorageException):
    code = 552
    message = "Cannot deactivate Logical Volume"


class CannotAccessLogicalVolume(StorageException):
    code = 553
    message = "Cannot access Logical Volume"


class LogicalVolumeExtendError(StorageException):
    def __init__(self, vgname, lvname, newsize):
        self.value = ("vgname=%s lvname=%s "
                      "newsize=%s" % (vgname, lvname, newsize))
    code = 554
    message = "Logical Volume extend failed"


class LogicalVolumesListError(StorageException):
    code = 555
    message = "Cannot get Logical Volumes list from Volume Group"


class LogicalVolumeRefreshError(StorageException):
    code = 556
    message = "Cannot refresh Logical Volume"


class LogicalVolumeScanError(StorageException):
    def __init__(self, vgname, lvname):
        self.value = "vgname=%s lvname=%s" % (vgname, lvname)
    code = 557
    message = "Logical volume scanning error"


class CannotActivateLogicalVolume(StorageException):
    code = 558
    message = "Cannot activate Logical Volume"


class LogicalVolumePermissionsError(ResourceException):
    code = 559
    message = "Cannot update/change logical volume permissions"


class LogicalVolumeAddTagError(StorageException):
    code = 560
    message = "Add tag to Logical Volume error"


class LogicalVolumeRemoveTagError(StorageException):
    code = 561
    message = "Remove tag from Logical Volume error"


class GetLogicalVolumeTagError(StorageException):
    code = 562
    message = "Cannot get tags of Logical Volumes"


class GetLogicalVolumesByTagError(StorageException):
    code = 563
    message = "Cannot get Logical Volumes with specific tag"


class GetAllLogicalVolumeTagsError(StorageException):
    code = 564
    message = "Cannot get tags of all Logical Volumes of Volume Group"


class GetLogicalVolumeDevError(StorageException):
    code = 565
    message = "Cannot get physical devices of logical volume"


class LogicalVolumeRenameError(StorageException):
    code = 566
    message = "Cannot rename Logical Volume"


class CannotWriteAccessLogialVolume(StorageException):
    def __init__(self, vgname, lvname):
        self.value = "vgname=%s lvname=%s" % (vgname, lvname)
    code = 567
    message = "Cannot access logical volume for write"


class CannotSetRWLogicalVolume(StorageException):
    def __init__(self, vgname, lvname, rw):
        self.value = "vgname=%s lvname=%s rw=%s" % (vgname, lvname, rw)
    code = 568
    message = "Cannot set Logical volume RW permission"


class LogicalVolumesScanError(StorageException):
    def __init__(self, vgname, lvs):
        self.value = "vgname=%s, lvs=%s" % (vgname, lvs)
    code = 569
    message = "Logical volume scanning error"


class CannotActivateLogicalVolumes(StorageException):
    code = 570
    message = "Cannot activate Logical Volumes"


class GetLogicalVolumeDataError(StorageException):
    code = 571
    message = "Cannot get Logical Volume Info"


class LogicalVolumeReplaceTagError(StorageException):
    code = 572
    message = "Replace Logical Volume tag error"


class BlockDeviceActionError(StorageException):
    code = 600
    message = "Error block device action"


class PhysDevInitializationError(StorageException):
    code = 601
    message = "Failed to initialize physical device"


class LVMSetupError(StorageException):
    code = 602
    message = "LVM setup failed"


class CouldNotRetrievePhysicalVolumeList(StorageException):
    code = 603
    message = "Could not retrieve pv list"


class LogicalVolumeAlreadyExists(StorageException):
    code = 604
    message = "Cannot create logical volume - already exists"


class CouldNotRetrieveLogicalVolumesList(StorageException):
    code = 605
    message = "Could not retrieve lv list"


class InaccessiblePhysDev(StorageException):
    def __init__(self, devices):
        self.value = "devices=%s" % (devices,)
    code = 606
    message = "Multipath cannot access physical device(s)"


class PartitionedPhysDev(StorageException):
    code = 607
    message = "Partitioned physical device"


class MkfsError(StorageException):
    code = 608
    message = "Cannot create filesystem on device"


class MissingTagOnLogicalVolume(StorageException):
    def __init__(self, lvname, tag):
        self.value = "lvname=%s tag=%s" % (lvname, tag)
    code = 609
    message = "Missing logical volume tag."


class LogicalVolumeDoesNotExistError(StorageException):
    code = 610
    message = "Logical volume does not exist"


class LogicalVolumeCachingError(StorageException):
    code = 611
    message = "Logical volume cache error"


class LogicalVolumeWrongTagError(StorageException):
    code = 612
    message = "Wrong logical volume tag"


class VgMetadataCriticallyFull(StorageException):
    def __init__(self, vgname, mdasize, mdafree):
        self.value = ("vgname=%s mdasize=%s "
                      "mdafree=%s" % (vgname, mdasize, mdafree))
    code = 613
    message = (
        "Error - The system has reached the high watermark on the VG "
        "metadata area size. This is due high number of Vdisks or "
        "large Vdisks size allocated on this specific VG. Please call "
        "Support to address the issue")


class SmallVgMetadata(StorageException):
    def __init__(self, vgname, mdasize, mdafree):
        self.value = ("vgname=%s mdasize=%s "
                      "mdafree=%s" % (vgname, mdasize, mdafree))
    code = 614
    message = (
        "Warning - The allocated VG metadata area size is too small, "
        "which might limit its capacity (the number of Vdisks and/or "
        "their size). Refer to GSS knowledge base to understand the "
        "issue and how to resolve it")


class CouldNotResizePhysicalVolume(StorageException):
    def __init__(self, pvname, err):
        self.value = "pvname=%s err=%s" % (pvname, err)
    code = 615
    message = "Could not resize PV"


class UnexpectedVolumeGroupMetadata(StorageException):
    def __init__(self, reason):
        self.value = "reason=%s" % reason
    code = 616
    message = "Volume Group metadata isn't as expected"


class ForbiddenPhysicalVolumeOperation(StorageException):
    code = 617
    message = "The operation couldn't be performed on the provided pv"

    def __init__(self, reason):
        self.value = "reason=%s" % reason


class CouldNotMovePVData(StorageException):
    code = 618
    message = "Could not move PV data, there might be leftovers that require" \
              " manual handling - please refer to the pvmove man page"

    def __init__(self, pvname, vgname, err):
        self.value = "pvname=%s vgname=%s err=%s" % (pvname, vgname, err)


class NoSuchPhysicalVolume(StorageException):
    code = 619
    message = "No such PV"

    def __init__(self, pvname, vgname):
        self.value = "pvname=%s vgname=%s" % (pvname, vgname)


class NoSuchDestinationPhysicalVolumes(StorageException):
    code = 620
    message = "No such destination PVs"

    def __init__(self, pvs, vgname):
        self.value = "pvs=%s vgname=%s" % (pvs, vgname)


#################################################
#  SPM/HSM Exceptions
#################################################

class SpmStartError(StorageException):
    code = 650
    message = "Error starting SPM"


class AcquireLockFailure(StorageException):
    def __init__(self, id, rc, out, err):
        self.value = "id=%s, rc=%s, out=%s, err=%s" % (id, rc, out, err)
    code = 651
    message = "Cannot obtain lock"


class SpmParamsMismatch(StorageException):
    def __init__(self, oldlver, oldid, prevLVER, prevID):
        self.value = ("expected previd:%s lver:%s "
                      "got request for previd:%s lver:%s" %
                      (oldid, oldlver, prevID, prevLVER))
    code = 652
    message = "Pool previous lver/id don't match request"


class SpmStopError(StorageException):
    def __init__(self, spUUID, strRunningTask=None):
        self.value = "spUUID=%s, task=%s" % (spUUID, strRunningTask)
    code = 653
    message = "Error stopping SPM, SPM has unfinished task(s)"


class SpmStatusError(StorageException):
    code = 654
    message = "Not SPM"

# Removed Exception. Commented for code number reference.
# class SpmFenceError(StorageException):
#     code = 655
#     message = "Error fencing SPM"


class IsSpm(StorageException):
    code = 656
    message = "Operation not allowed while SPM is active"


class DomainAlreadyLocked(StorageException):
    code = 657
    message = "Cannot acquire lock, resource marked as locked"


class DomainLockDoesNotExist(StorageException):
    code = 658
    message = "Cannot release lock, resource not found"


# Removed Exception. Commented for code number reference.
# class CannotRetrieveSpmStatus(StorageException):
#     code = 659
#     message = ("Cannot retrieve SPM status, master domain probably "
#                "unavailable")


class ReleaseLockFailure(StorageException):
    code = 660
    message = "Cannot release lock"


class AcquireHostIdFailure(StorageException):
    code = 661
    message = "Cannot acquire host id"


class ReleaseHostIdFailure(StorageException):
    code = 662
    message = "Cannot release host id"


class HostIdMismatch(StorageException):
    code = 700
    message = "Host id not found or does not match manager host id"


class ClusterLockInitError(StorageException):
    code = 701
    message = "Could not initialize cluster lock"


class InquireNotSupportedError(StorageException):
    code = 702
    message = "Cluster lock inquire isnt supported"


#################################################
#  Meta data related Exceptions
#################################################

class MetaDataGeneralError(StorageException):
    code = 749
    message = "General Meta data error"


class MetaDataKeyError(MetaDataGeneralError):
    code = 750
    message = "Meta data key error"


class MetaDataKeyNotFoundError(MetaDataGeneralError):
    code = 751
    message = "Meta Data key not found error"


class MetaDataSealIsBroken(MetaDataGeneralError):
    def __init__(self, cksum, computed_cksum):
        self.value = ("cksum = %s, "
                      "computed_cksum = %s" % (cksum, computed_cksum))
    code = 752
    message = "Meta Data seal is broken (checksum mismatch)"


class MetaDataValidationError(MetaDataGeneralError):
    code = 753
    message = "Meta Data self-validation failed"


class MetaDataMappingError(MetaDataGeneralError):
    code = 754
    message = "Meta Data mapping failed"


# Removed Exception. Commented for code number reference.
# class MetaDataParamError(MetaDataGeneralError):
#     code = 755
#     message = "Meta Data parameter invalid"


class MetadataOverflowError(MetaDataGeneralError):
    code = 756
    message = "Metadata is too big. Cannot change Metadata"

    def __init__(self, data):
        self.value = "data=%r" % data


class MetadataCleared(MetaDataKeyNotFoundError):
    code = 757
    message = "Metadata was cleared, volume is partly deleted"


#################################################
#  Import/Export Exceptions
#################################################

class ImportError(StorageException):
    code = 800
    message = "Error importing image"


class ImportInfoError(StorageException):
    code = 801
    message = "Import candidate info error"


class ImportUnknownType(StorageException):
    code = 802
    message = "Unknown import type"


class ExportError(StorageException):
    code = 803
    message = "Error exporting VM"


#################################################
#  Resource Exceptions
#################################################

class ResourceNamespaceNotEmpty(GeneralException):
    code = 850
    message = "Resource Namespace is not empty"


class ResourceTimeout(GeneralException):
    code = 851
    message = "Resource timeout"


class ResourceDoesNotExist(GeneralException):
    code = 852
    message = "Resource does not exist"


class InvalidResourceName(GeneralException):
    code = 853
    message = "Invalid resource name"


class ResourceReferenceInvalid(GeneralException):
    code = 854
    message = ("Cannot perform operation. This resource has been released or "
               "expired.")


class ResourceAcqusitionFailed(GeneralException):
    code = 855
    message = ("Could not acquire resource. "
               "Probably resource factory threw an exception.")


#################################################
#  External domains exceptions
#  Range: 900-909
#################################################

class StorageDomainIsMemberOfPool(StorageException):
    code = 900
    message = "Storage domain is member of pool"

    def __init__(self, sdUUID):
        self.value = "domain=%s" % (sdUUID,)


#################################################
#  SDM Errors
#  Range: 910-919
#################################################

class DomainHasGarbage(StorageException):
    code = 910
    message = "Operation failed because garbage was found"

    def __init__(self, reason):
        self.value = reason


class GenerationMismatch(StorageException):
    code = 911
    message = "The provided generation does not match the actual generation"

    def __init__(self, requested, actual):
        self.value = "requested=%s, actual=%s" % (requested, actual)


class VolumeIsNotInChain(StorageException):
    code = 920
    message = "Volume is not part of the chain."

    def __init__(self, sd_id, img_id, vol_id):
        self.value = ("sd_id=%s, img_id=%s, vol_id=%s" %
                      (vol_id, sd_id, img_id))


class WrongParentVolume(StorageException):
    code = 921
    message = "Wrong parent volume."

    def __init__(self, vol_id, parent_id):
        self.value = "vol_id=%s, parent_id=%s" % (vol_id, parent_id)


class UnexpectedVolumeState(StorageException):
    code = 922
    message = "Unexpected volume state."

    def __init__(self, base_vol_id, expected, actual):
        self.value = ("vol_id=%s, expected=%s, actual=%s" % (
                      base_vol_id, expected, actual))


#################################################
#  Managed Volume Errors
#  Range: 925-935
#################################################


class ManagedVolumeNotSupported(StorageException):
    code = 925
    message = ("Managed Volume Not Supported. "
               "Missing package os-brick.")


class ManagedVolumeHelperFailed(StorageException):
    code = 926
    message = "Managed Volume Helper failed."


class ManagedVolumeAlreadyAttached(StorageException):
    code = 927
    message = "Managed Volume is already attached."

    def __init__(self, vol_id, path, attachment):
        self.value = "vol_id=%s path=%s attachment=%s" % (
            vol_id, path, attachment)


class ManagedVolumeUnsupportedDevice(StorageException):
    code = 928
    message = "Unsupported Device: missing multipath_id"

    def __init__(self, vol_id, attachment):
        self.value = "vol_id=%s attachment=%s" % (vol_id, attachment)


class ManagedVolumeConnectionMismatch(StorageException):
    code = 929
    message = "Attach existing volume with different connection information"

    def __init__(self, vol_id, expected, actual):
        self.value = "vol_id=%s expected=%s actual=%s" % (
            vol_id, expected, actual)


#################################################
#  VM leases Errors
#  Range: 936-940
#################################################


class NoSuchLease(StorageException):
    code = 936
    message = "No such lease"
    expected = True

    def __init__(self, lease_id):
        self.value = "lease={}".format(lease_id)
