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

import os.path
import logging
import time
import signal
from contextlib import contextmanager

import image
from vdsm import constants
import storage_exception as se
import sd
from sdc import sdCache
import misc
from misc import deprecated
import fileUtils
import task
from threadLocal import vars
import resourceFactories
import resourceManager as rm
rmanager = rm.ResourceManager.getInstance()


DOMAIN_MNT_POINT = 'mnt'

# Volume Types
UNKNOWN_VOL = 0
PREALLOCATED_VOL = 1
SPARSE_VOL = 2

# Volume Format
UNKNOWN_FORMAT = 3
COW_FORMAT = 4
RAW_FORMAT = 5

# Volume Role
SHARED_VOL = 6
INTERNAL_VOL = 7
LEAF_VOL = 8

VOL_TYPE = [PREALLOCATED_VOL, SPARSE_VOL]
VOL_FORMAT = [COW_FORMAT, RAW_FORMAT]
VOL_ROLE = [SHARED_VOL, INTERNAL_VOL, LEAF_VOL]

VOLUME_TYPES = {UNKNOWN_VOL: 'UNKNOWN', PREALLOCATED_VOL: 'PREALLOCATED',
                SPARSE_VOL: 'SPARSE',
                UNKNOWN_FORMAT: 'UNKNOWN', COW_FORMAT: 'COW',
                RAW_FORMAT: 'RAW',
                SHARED_VOL: 'SHARED', INTERNAL_VOL: 'INTERNAL',
                LEAF_VOL: 'LEAF'}

BLANK_UUID = misc.UUID_BLANK

# Volume meta data fields
SIZE = "SIZE"
TYPE = "TYPE"
FORMAT = "FORMAT"
DISKTYPE = "DISKTYPE"
VOLTYPE = "VOLTYPE"
PUUID = "PUUID"
DOMAIN = "DOMAIN"
CTIME = "CTIME"
IMAGE = "IMAGE"
DESCRIPTION = "DESCRIPTION"
LEGALITY = "LEGALITY"
MTIME = "MTIME"

ILLEGAL_VOL = "ILLEGAL"
LEGAL_VOL = "LEGAL"
FAKE_VOL = "FAKE"

log = logging.getLogger('Storage.Volume')

FMT2STR = {COW_FORMAT: 'qcow2', RAW_FORMAT: 'raw'}


def fmt2str(format):
    return FMT2STR[format]


def type2name(volType):
    try:
        return VOLUME_TYPES[volType]
    except IndexError:
        return None


def name2type(name):
    for (k, v) in VOLUME_TYPES.iteritems():
        if v == name.upper():
            return k
    return None


class Volume(object):
    log = logging.getLogger('Storage.Volume')

    def __init__(self, repoPath, sdUUID, imgUUID, volUUID):
        self.repoPath = repoPath
        self.sdUUID = sdUUID
        self.imgUUID = imgUUID
        self.volUUID = volUUID
        self.volumePath = None
        self.imagePath = None
        if not imgUUID or imgUUID == BLANK_UUID:
            raise se.InvalidParameterException("imgUUID", imgUUID)
        if not volUUID or volUUID == BLANK_UUID:
            raise se.InvalidParameterException("volUUID", volUUID)
        self.voltype = None
        self.validate()

    @classmethod
    def _getModuleAndClass(cls):
        clsName = cls.__name__
        clsModule = cls.__module__.split(".").pop()
        return clsModule, clsName

    def validate(self):
        """
        Validate that the volume can be accessed
        """
        self.validateImagePath()
        self.validateVolumePath()

    def __str__(self):
        return str(self.volUUID)

    @classmethod
    def killProcRollback(cls, taskObj, pid, ctime):
        """
        First part of several volume rollbacks.
        """
        cls.log.info("pid=%s ctime=%s", pid, ctime)
        try:
            pidCtime = misc.getProcCtime(pid)
        except OSError, e:
            cls.log.debug("pid=%s ctime=%s (%s)", pid, ctime, str(e))
            return

        try:
            # If process exists and it's a same process kill it
            # We identifying the process according its pid and ctime
            if ctime == pidCtime:
                os.kill(int(pid), signal.SIGKILL)
        except Exception:
            cls.log.error("pid=%s ctime=%s", pid, ctime, exc_info=True)
            raise

    @classmethod
    def rebaseVolumeRollback(cls, taskObj, sdUUID, srcImg,
                             srcVol, dstFormat, srcParent, unsafe):
        """
        Rebase volume rollback
        """
        cls.log.info("sdUUID=%s srcImg=%s srcVol=%s dstFormat=%s srcParent=%s",
                     sdUUID, srcImg, srcVol, dstFormat, srcParent)

        imageResourcesNamespace = sd.getNamespace(
                                       sdUUID,
                                       resourceFactories.IMAGE_NAMESPACE)
        with rmanager.acquireResource(imageResourcesNamespace,
                                      srcImg, rm.LockType.exclusive):
            try:
                vol = sdCache.produce(sdUUID).produceVolume(imgUUID=srcImg,
                                                            volUUID=srcVol)
                vol.prepare(rw=True, chainrw=True, setrw=True)
            except Exception:
                cls.log.error(
                    "sdUUID=%s srcImg=%s srcVol=%s dstFormat=%s srcParent=%s",
                    sdUUID, srcImg, srcVol, dstFormat, srcParent,
                    exc_info=True)
                raise

            try:
                (rc, out, err) = qemuRebase(
                                      vol.getVolumePath(), vol.getFormat(),
                                      os.path.join('..', srcImg, srcParent),
                                      int(dstFormat), misc.parseBool(unsafe),
                                      vars.task.aborting, False)
                if rc:
                    raise se.MergeVolumeRollbackError(srcVol)

                vol.setParent(srcParent)
                vol.recheckIfLeaf()
            except Exception:
                cls.log.error(
                    "sdUUID=%s srcImg=%s srcVol=%s dstFormat=%s srcParent=%s",
                    sdUUID, srcImg, srcVol, dstFormat, srcParent,
                    exc_info=True)
                raise
            finally:
                vol.teardown(sdUUID, srcVol)

    def rebase(self, backingVol, backingVolPath,
               backingFormat, unsafe, rollback):
        """
        Rebase volume on top of new backing volume
        """
        if rollback:
            pvol = self.getParentVolume()
            if not pvol:
                self.log.warn("Can't rebase volume %s, parent missing",
                              self.volUUID)
                return

            name = "Merge volume: " + self.volUUID
            vars.task.pushRecovery(
                        task.Recovery(name, "volume", "Volume",
                                      "rebaseVolumeRollback",
                                      [self.sdUUID, self.getImage(),
                                       self.volUUID, str(pvol.getFormat()),
                                       pvol.volUUID, str(True)]))

        (rc, out, err) = qemuRebase(self.getVolumePath(), self.getFormat(),
                                    backingVolPath, backingFormat, unsafe,
                                    vars.task.aborting, rollback)
        if rc:
            raise se.MergeSnapshotsError(self.volUUID)
        self.setParent(backingVol)
        self.recheckIfLeaf()

    def clone(self, dst_image_dir, dst_volUUID, volFormat, preallocate):
        """
        Clone self volume to the specified dst_image_dir/dst_volUUID
        """
        wasleaf = False
        dst_path = None
        taskName = "parent volume rollback: " + self.volUUID
        vars.task.pushRecovery(
                    task.Recovery(taskName, "volume", "Volume",
                                  "parentVolumeRollback",
                                  [self.sdUUID, self.imgUUID, self.volUUID]))
        if self.isLeaf():
            wasleaf = True
            self.setInternal()
        try:
            self.prepare(rw=False)
            dst_path = os.path.join(dst_image_dir, dst_volUUID)
            self.log.debug("Volume.clone: %s to %s" %
                           (self.volumePath, dst_path))
            size = int(self.getMetaParam(SIZE))
            parent = self.getVolumePath()
            parent_format = fmt2str(self.getFormat())
            # We should use parent's relative path instead of full path
            parent = os.path.join(os.path.basename(os.path.dirname(parent)),
                                                   os.path.basename(parent))
            createVolume(parent, parent_format, dst_path,
                         size, volFormat, preallocate)
            self.teardown(self.sdUUID, self.volUUID)
        except Exception, e:
            # FIXME: might race with other clones
            if wasleaf:
                self.setLeaf()
            self.teardown(self.sdUUID, self.volUUID)
            self.log.error("Volume.clone: can't clone: %s to %s" %
                           (self.volumePath, dst_path))
            raise se.CannotCloneVolume(self.volumePath, dst_path, str(e))

    def _shareLease(self, dstImgPath):
        """
        Internal utility method used during the share process and by the
        domain V3 upgrade.
        """
        pass  # Do not remove this method or the V3 upgrade will fail.

    def share(self, dstImgPath):
        """
        Share this volume to dstImgPath
        """
        self.log.debug("Share volume %s to %s", self.volUUID, dstImgPath)

        if not self.isShared():
            raise se.VolumeNonShareable(self)

        if os.path.basename(dstImgPath) == os.path.basename(self.imagePath):
            raise se.VolumeOwnershipError(self)

        dstPath = os.path.join(dstImgPath, self.volUUID)
        clsModule, clsName = type(self)._getModuleAndClass()

        try:
            vars.task.pushRecovery(
                task.Recovery("Share volume rollback: %s" % dstPath, clsModule,
                              clsName, "shareVolumeRollback", [dstPath])
            )

            self._share(dstImgPath)

        except Exception, e:
            raise se.CannotShareVolume(self.getVolumePath(), dstPath, str(e))

    def refreshVolume(self):
        """
        Refresh volume
        """
        pass

    @classmethod
    def getVSize(cls, sdUUID, imgUUID, volUUID, bs=512):
        """
        Return volume size
        """
        mysd = sdCache.produce(sdUUID=sdUUID)
        return mysd.getVolumeClass().getVSize(mysd, imgUUID, volUUID, bs)

    @classmethod
    def getVTrueSize(cls, sdUUID, imgUUID, volUUID, bs=512):
        """
        Return allocated volume size
        """
        mysd = sdCache.produce(sdUUID=sdUUID)
        return mysd.getVolumeClass().getVTrueSize(mysd, imgUUID, volUUID, bs)

    @classmethod
    def parentVolumeRollback(cls, taskObj, sdUUID, pimgUUID, pvolUUID):
        cls.log.info("parentVolumeRollback: sdUUID=%s pimgUUID=%s"
                     " pvolUUID=%s" % (sdUUID, pimgUUID, pvolUUID))
        try:
            if pvolUUID != BLANK_UUID and pimgUUID != BLANK_UUID:
                pvol = sdCache.produce(sdUUID).produceVolume(pimgUUID,
                                                             pvolUUID)
                if not pvol.isShared() and not pvol.recheckIfLeaf():
                    pvol.setLeaf()
                pvol.teardown(sdUUID, pvolUUID)
        except Exception:
            cls.log.error("Unexpected error", exc_info=True)

    @classmethod
    def startCreateVolumeRollback(cls, taskObj, sdUUID, imgUUID, volUUID):
        cls.log.info("startCreateVolumeRollback: sdUUID=%s imgUUID=%s "
                     "volUUID=%s " % (sdUUID, imgUUID, volUUID))
        # This rollback doesn't actually do anything.
        # In general the createVolume rollbacks are a list of small rollbacks
        # that are replaced by the one major rollback at the end of the task.
        # This rollback is a simple marker that must be the first rollback
        # in the list of createVolume rollbacks.
        # We need it in cases when createVolume is part of a composite task and
        # not a task by itself. In such cases when we will replace the list of
        # small rollbacks with the major one, we want to be able remove only
        # the relevant rollbacks from the rollback list.
        pass

    @classmethod
    def createVolumeRollback(cls, taskObj, repoPath,
                             sdUUID, imgUUID, volUUID, imageDir):
        cls.log.info("createVolumeRollback: repoPath=%s sdUUID=%s imgUUID=%s "
                     "volUUID=%s imageDir=%s" %
                     (repoPath, sdUUID, imgUUID, volUUID, imageDir))
        vol = sdCache.produce(sdUUID).produceVolume(imgUUID, volUUID)
        # Avoid rollback if volume has children
        if len(vol.getChildrenList()):
            raise se.createVolumeRollbackError(volUUID)
        pvol = vol.getParentVolume()
        # Remove volume
        vol.delete(postZero=False, force=True)
        if len(cls.getImageVolumes(repoPath, sdUUID, imgUUID)):
            # Don't remove the image folder itself
            return

        if not pvol or pvol.isShared():
            # Remove image folder with all leftovers
            if os.path.exists(imageDir):
                fileUtils.cleanupdir(imageDir)

    @classmethod
    def validateCreateVolumeParams(cls, volFormat, preallocate, srcVolUUID):
        """
        Validate create volume parameters
        """
        if volFormat not in VOL_FORMAT:
            raise se.IncorrectFormat(type2name(volFormat))

        if preallocate not in VOL_TYPE:
            raise se.IncorrectType(type2name(preallocate))

    @classmethod
    def create(cls, repoPath, sdUUID, imgUUID, size, volFormat, preallocate,
               diskType, volUUID, desc, srcImgUUID, srcVolUUID):
        """
        Create a new volume with given size or snapshot
            'size' - in sectors
            'volFormat' - volume format COW / RAW
            'preallocate' - Preallocate / Sparse
            'diskType' - enum (API.Image.DiskTypes)
            'srcImgUUID' - source image UUID
            'srcVolUUID' - source volume UUID
        """
        # Validate volume parameters should be checked here for all
        # internal flows using volume creation.
        misc.validateUUID(sdUUID, "sdUUID", False)
        misc.validateUUID(imgUUID, "imgUUID", False)
        misc.validateUUID(srcImgUUID, "srcImgUUID", True)
        misc.validateUUID(srcVolUUID, "srcVolUUID", True)
        cls.validateCreateVolumeParams(volFormat, preallocate, srcVolUUID)

        dom = sdCache.produce(sdUUID)
        imgPath = image.Image(repoPath).create(sdUUID, imgUUID)

        if dom.volumeExists(imgPath, volUUID):
            raise se.VolumeAlreadyExists(volUUID)

        volPath = os.path.join(imgPath, volUUID)
        volParent = None
        volType = type2name(LEAF_VOL)

        # Get the specific class name and class module to be used in the
        # Recovery tasks.
        clsModule, clsName = cls._getModuleAndClass()

        try:
            if srcVolUUID != BLANK_UUID:
                # When the srcImgUUID isn't specified we assume it's the same
                # as the imgUUID
                if srcImgUUID == BLANK_UUID:
                    srcImgUUID = imgUUID

                volParent = cls(repoPath, sdUUID, srcImgUUID, srcVolUUID)

                if not volParent.isLegal():
                    raise se.createIllegalVolumeSnapshotError(
                            volParent.volUUID)

                if imgUUID != srcImgUUID:
                    volParent.share(imgPath)
                    volParent = cls(repoPath, sdUUID, imgUUID, srcVolUUID)

                # Override the size with the size of the parent
                size = volParent.getSize()

        except se.StorageException:
            cls.log.error("Unexpected error", exc_info=True)
            raise
        except Exception, e:
            cls.log.error("Unexpected error", exc_info=True)
            raise se.VolumeCannotGetParent("Couldn't get parent %s for "
                                "volume %s: %s" % (srcVolUUID, volUUID, e))

        try:
            cls.log.info("Creating volume %s", volUUID)

            # Rollback sentinel to mark the start of the task
            vars.task.pushRecovery(
                task.Recovery(task.ROLLBACK_SENTINEL, clsModule, clsName,
                              "startCreateVolumeRollback",
                              [sdUUID, imgUUID, volUUID])
            )

            # Create volume rollback
            vars.task.pushRecovery(
                task.Recovery("Halfbaked volume rollback", clsModule, clsName,
                              "halfbakedVolumeRollback",
                              [sdUUID, volUUID, volPath])
            )

            # Specific volume creation (block, file, etc...)
            metaId = cls._create(dom, imgUUID, volUUID, size, volFormat,
                                 preallocate, volParent, srcImgUUID,
                                 srcVolUUID, imgPath, volPath)

            # When the volume format is raw what the guest sees is the apparent
            # size of the file/device therefore if the requested size doesn't
            # match the apparent size (eg: physical extent granularity in LVM)
            # we need to update the size value so that the metadata reflects
            # the correct state.
            if volFormat == RAW_FORMAT:
                apparentSize = cls.getVSize(dom, imgUUID, volUUID)
                if apparentSize < size:
                    cls.log.error("The volume %s apparent size %s is smaller "
                        "than the requested size %s", volUUID, apparentSize,
                        size)
                    raise se.VolumeCreationError()
                if apparentSize > size:
                    cls.log.info("The requested size for volume %s doesn't "
                        "match the granularity on domain %s, updating the "
                        "volume size from %s to %s", volUUID, sdUUID, size,
                        apparentSize)
                    size = apparentSize

            vars.task.pushRecovery(
                task.Recovery("Create volume metadata rollback", clsModule,
                              clsName, "createVolumeMetadataRollback",
                              map(str, metaId))
            )

            cls.newMetadata(metaId, sdUUID, imgUUID, srcVolUUID, size,
                            type2name(volFormat), type2name(preallocate),
                            volType, diskType, desc, LEGAL_VOL)
            cls.newVolumeLease(metaId, sdUUID, volUUID)

        except se.StorageException:
            cls.log.error("Unexpected error", exc_info=True)
            raise
        except Exception, e:
            cls.log.error("Unexpected error", exc_info=True)
            raise se.VolumeCreationError("Volume creation %s failed: %s" %
                                         (volUUID, e))

        # Remove the rollback for the halfbaked volume
        vars.task.replaceRecoveries(
            task.Recovery("Create volume rollback", clsModule, clsName,
                          "createVolumeRollback",
                          [repoPath, sdUUID, imgUUID, volUUID, imgPath])
        )

        return volUUID

    def validateDelete(self):
        """
        Validate volume before deleting
        """
        try:
            if self.isShared():
                raise se.CannotDeleteSharedVolume("img %s vol %s" %
                                                  (self.imgUUID, self.volUUID))
            children = self.getChildrenList()
            if len(children) > 0:
                raise se.VolumeImageHasChildren(self)
        except se.MetaDataKeyNotFoundError, e:
            # In case of metadata key error, we have corrupted
            # volume (One of metadata corruptions may be
            # previous volume deletion failure).
            # So, there is no reasons to avoid its deletion
            self.log.warn("Volume %s metadata error (%s)",
                          self.volUUID, str(e))

    def extend(self, newsize):
        """
        Extend a logical volume
        """
        pass

    def setDescription(self, descr):
        """
        Set Volume Description
            'descr' - volume description
        """
        self.log.info("volUUID = %s descr = %s ", self.volUUID, descr)
        self.setMetaParam(DESCRIPTION, descr)

    def getDescription(self):
        """
        Return volume description
        """
        return self.getMetaParam(DESCRIPTION)

    def getLegality(self):
        """
        Return volume legality
        """
        try:
            legality = self.getMetaParam(LEGALITY)
            return legality
        except se.MetaDataKeyNotFoundError:
            return LEGAL_VOL

    def setLegality(self, legality):
        """
        Set Volume Legality
            'legality' - volume legality
        """
        self.log.info("sdUUID=%s imgUUID=%s volUUID = %s legality = %s ",
                      self.sdUUID, self.imgUUID, self.volUUID, legality)
        self.setMetaParam(LEGALITY, legality)

    def setDomain(self, sdUUID):
        self.setMetaParam(DOMAIN, sdUUID)
        self.sdUUID = sdUUID
        return self.sdUUID

    def setShared(self):
        self.setMetaParam(VOLTYPE, type2name(SHARED_VOL))
        self.voltype = type2name(SHARED_VOL)
        self.setrw(rw=False)
        return self.voltype

    @deprecated  # valid for domain version < 3
    def setrw(self, rw):
        # Since domain version 3 (V3) VDSM is not changing the internal volumes
        # permissions to read-only because it would interfere with the live
        # snapshots and the live merge processes. E.g.: during a live snapshot
        # if the VM is running on the SPM it would lose the ability to write to
        # the current volume.
        # However to avoid lvm MDA corruption we still need to set the volume
        # as read-only on domain version 2. The corruption is triggered on the
        # HSMs that are using the resource manager to prepare the volume chain.
        if int(sdCache.produce(self.sdUUID).getVersion()) < 3:
            self._setrw(rw=rw)

    def setLeaf(self):
        self.setMetaParam(VOLTYPE, type2name(LEAF_VOL))
        self.voltype = type2name(LEAF_VOL)
        self.setrw(rw=True)
        return self.voltype

    def setInternal(self):
        self.setMetaParam(VOLTYPE, type2name(INTERNAL_VOL))
        self.voltype = type2name(INTERNAL_VOL)
        self.setrw(rw=False)
        return self.voltype

    def getVolType(self):
        if not self.voltype:
            self.voltype = self.getMetaParam(VOLTYPE)
        return self.voltype

    def getSize(self):
        return int(self.getMetaParam(SIZE))

    def setSize(self, size):
        self.setMetaParam(SIZE, size)

    def getType(self):
        return name2type(self.getMetaParam(TYPE))

    def setType(self, prealloc):
        self.setMetaParam(TYPE, type2name(prealloc))

    def getDiskType(self):
        return self.getMetaParam(DISKTYPE)

    def getFormat(self):
        return name2type(self.getMetaParam(FORMAT))

    def setFormat(self, volFormat):
        self.setMetaParam(FORMAT, type2name(volFormat))

    def isLegal(self):
        try:
            legality = self.getMetaParam(LEGALITY)
            return legality != ILLEGAL_VOL
        except se.MetaDataKeyNotFoundError:
            return True

    def isFake(self):
        try:
            legality = self.getMetaParam(LEGALITY)
            return legality == FAKE_VOL
        except se.MetaDataKeyNotFoundError:
            return False

    def isShared(self):
        return self.getVolType() == type2name(SHARED_VOL)

    def isLeaf(self):
        return self.getVolType() == type2name(LEAF_VOL)

    def isInternal(self):
        return self.getVolType() == type2name(INTERNAL_VOL)

    def isSparse(self):
        return self.getType() == SPARSE_VOL

    def recheckIfLeaf(self):
        """
        Recheck if I am a leaf.
        """

        if self.isShared():
            return False

        type = self.getVolType()
        childrenNum = len(self.getChildrenList())

        if childrenNum == 0 and type != LEAF_VOL:
            self.setLeaf()
        elif childrenNum > 0 and type != INTERNAL_VOL:
            self.setInternal()

        return self.isLeaf()

    @contextmanager
    def scopedPrepare(self, rw=True, justme=False, chainrw=False, setrw=False,
                 force=False):
        self.prepare(rw=True, justme=False, chainrw=False, setrw=False,
                     force=False)
        try:
            yield self
        finally:
            self.teardown(self.sdUUID, self.volUUID, justme)

    def prepare(self, rw=True, justme=False,
                chainrw=False, setrw=False, force=False):
        """
        Prepare volume for use by consumer.
        If justme is false, the entire COW chain is prepared.
        Note: setrw arg may be used only by SPM flows.
        """
        self.log.info("Volume: preparing volume %s/%s",
                      self.sdUUID, self.volUUID)

        if not force:
            # Cannot prepare ILLEGAL volume
            if not self.isLegal():
                raise se.prepareIllegalVolumeError(self.volUUID)

            if rw and self.isShared():
                if chainrw:
                    rw = False      # Shared cannot be set RW
                else:
                    raise se.SharedVolumeNonWritable(self)

            if (not chainrw and rw and self.isInternal() and setrw and
                not self.recheckIfLeaf()):
                raise se.InternalVolumeNonWritable(self)

        self.llPrepare(rw=rw, setrw=setrw)
        try:
            # Mtime is the time of the last prepare for RW
            if rw:
                self.setMetaParam(MTIME, int(time.time()))
            if justme:
                return True
            pvol = self.getParentVolume()
            if pvol:
                pvol.prepare(rw=chainrw, justme=False,
                             chainrw=chainrw, setrw=setrw)
        except Exception, e:
            self.log.error("Unexpected error", exc_info=True)
            self.teardown(self.sdUUID, self.volUUID)
            raise e

        return True

    @classmethod
    def teardown(cls, sdUUID, volUUID, justme=False):
        """
        Teardown volume.
        If justme is false, the entire COW chain is teared down.
        """
        pass

    def metadata2info(self, meta):
        return {
            "uuid": self.volUUID,
            "type": meta.get(TYPE, ""),
            "format":   meta.get(FORMAT, ""),
            "disktype": meta.get(DISKTYPE, ""),
            "voltype":  meta.get(VOLTYPE, ""),
            "size": int(meta.get(SIZE, "0")),
            "parent":   self.getParent(),
            "description":  meta.get(DESCRIPTION, ""),
            "pool": meta.get(sd.DMDK_POOLS, ""),
            "domain":   meta.get(DOMAIN, ""),
            "image":    self.getImage(),
            "ctime":    meta.get(CTIME, ""),
            "mtime":    meta.get(MTIME, ""),
            "legality": meta.get(LEGALITY, ""),
        }

    @classmethod
    def newMetadata(cls, metaId, sdUUID, imgUUID, puuid, size, format, type,
                    voltype, disktype, desc="", legality=ILLEGAL_VOL):
        meta = {
            FORMAT: str(format),
            TYPE:   str(type),
            VOLTYPE:    str(voltype),
            DISKTYPE:   str(disktype),
            SIZE:   int(size),
            CTIME:  int(time.time()),
            sd.DMDK_POOLS:  "",  # obsolete
            DOMAIN: str(sdUUID),
            IMAGE:  str(imgUUID),
            DESCRIPTION:    str(desc),
            PUUID:  str(puuid),
            MTIME:  int(time.time()),
            LEGALITY:   str(legality),
        }

        cls.createMetadata(metaId, meta)
        return meta

    def getInfo(self):
        """
        Get volume info
        """
        self.log.info("Info request: sdUUID=%s imgUUID=%s volUUID = %s ",
                      self.sdUUID, self.imgUUID, self.volUUID)
        image_corrupted = False
        info = {}
        try:
            meta = self.getMetadata()
            info = self.metadata2info(meta)
            info["capacity"] = str(int(info["size"]) * 512)
            del info["size"]
            # Get the image actual size on disk
            vsize = self.getVolumeSize(bs=1)
            avsize = self.getVolumeTrueSize(bs=1)
            info['apparentsize'] = str(vsize)
            info['truesize'] = str(avsize)
            info['mtime'] = self.getVolumeMtime()
            info['status'] = "OK"
        except se.StorageException, e:
            self.log.debug("exception: %s:%s" % (str(e.message), str(e.value)))
            info['apparentsize'] = "0"
            info['truesize'] = "0"
            info['mtime'] = "0"
            info['status'] = "INVALID"

        info['children'] = self.getChildrenList()

        # If image was set to illegal, mark the status same
        # (because of VDC constraints)
        if info.get('legality', None) == ILLEGAL_VOL or image_corrupted:
            info['status'] = ILLEGAL_VOL
        self.log.info("%s/%s/%s info is %s",
                      self.sdUUID, self.imgUUID, self.volUUID, str(info))
        return info

    def getChildrenList(self):
        """
        Fetch the list of children volumes (in single image)
        """
        vols = self.getImageVolumes(self.repoPath, self.sdUUID, self.imgUUID)
        children = []
        for v in vols:
            if (sdCache.produce(self.sdUUID).
                produceVolume(self.imgUUID, v).getParent() == self.volUUID):
                children.append(v)
        return children

    def getParentVolume(self):
        """
        Return parent volume object
        """
        puuid = self.getParent()
        if puuid and puuid != BLANK_UUID:
            return sdCache.produce(self.sdUUID).produceVolume(self.imgUUID,
                                                              puuid)
        return None

    def getVolumePath(self):
        """
        Get the path of the volume file/link
        """
        if not self.volumePath:
            raise se.VolumeAccessError(self.volUUID)
        return self.volumePath

    def getMetaParam(self, key):
        """
        Get a value of a specific key
        """
        meta = self.getMetadata()
        try:
            return meta[key]
        except KeyError:
            raise se.MetaDataKeyNotFoundError(str(meta) + ":" + str(key))

    def setMetaParam(self, key, value):
        """
        Set a value of a specific key
        """
        meta = self.getMetadata()
        try:
            meta[str(key)] = str(value)
            self.setMetadata(meta)
        except Exception:
            self.log.error("Volume.setMetaParam: %s: %s=%s" %
                           (self.volUUID, key, value))
            raise

    def getVolumeTrueSize(self, bs=512):
        """
        Return the size of the storage allocated for this volume
        on underlying storage
        """
        pass

    def getVolumeParams(self, bs=512):
        volParams = {}
        volParams['volUUID'] = self.volUUID
        volParams['imgUUID'] = self.getImage()
        volParams['path'] = self.getVolumePath()
        volParams['disktype'] = self.getDiskType()
        volParams['prealloc'] = self.getType()
        volParams['volFormat'] = self.getFormat()
        # TODO: getSize returns size in 512b multiples, should move all sizes
        # to byte multiples everywhere to avoid conversion errors and change
        # only at the end
        volParams['size'] = self.getSize()
        volParams['apparentsize'] = self.getVolumeSize(bs=bs)
        volParams['truesize'] = self.getVolumeTrueSize(bs=bs)
        volParams['parent'] = self.getParent()
        volParams['descr'] = self.getDescription()
        volParams['legality'] = self.getLegality()
        return volParams


def createVolume(parent, parent_format, volume, size, format, prealloc):
    """
     --- Create new volume.
        'parent' - backing volume name
        'parent_format' - backing volume format
        'volume' - new volume name
        'format' - volume format [ 'COW' or 'RAW' ]
        'size' - in sectors, always multiple of the grain size (64KB)
        'preallocate' - flag PREALLOCATED_VOL/SPARSE_VOL,
                        defines actual storage device type.
                        PREALLOCATED_VOL = preallocated storage using
                        non-sparse format (+ DD for file, use raw LV for SAN)

    # SAN

      Prealloc/RAW = Normal LV (if snapshot => create copy of LV)
      Sparse/RAW = if snapshot create LVM snapshot
                (in the future, use storage backend thin provisioning),
                else create Normal LV <== Not supported
      Prealloc/COW = build qcow2 image within a preallocated space -
                     used only for COPY
      Sparse/COW = QCOW2 over LV

    # File

      Prealloc/RAW = Normal file + DD (implicit pre-zero)
      Sparse/RAW = Normal file (touch)
      Prealloc/COW = QCOW2 + DD <== Not supported
      Sparse/COW = QCOW2

    """
    # TODO: accept size only in bytes and convert before call to qemu-img
    cmd = [constants.EXT_QEMUIMG, "create", "-f", fmt2str(format)]
    cwd = None
    if format == COW_FORMAT and parent:
        # cmd += ["-b", parent, volume]
        # cwd = os.path.split(os.path.split(volume)[0])[0]

        # Temporary fix for qemu-img creation problem
        cmd += ["-F", parent_format, "-b", os.path.join("..", parent), volume]
        cwd = os.path.split(volume)[0]
    else:
        size = int(size)
        if size < 1:
            raise se.createVolumeSizeError()

        # qemu-img expects size to be in kilobytes by default,
        # can also accept size in M or G with appropriate suffix
        # +1 is so that odd numbers will round upwards.
        cmd += [volume, "%uK" % ((size + 1) / 2)]

    (rc, out, err) = misc.execCmd(cmd, sudo=False, cwd=cwd)
    if rc:
        raise se.VolumeCreationError(out)
    return True


def baseAsyncTasksRollback(proc):
    name = "Kill-" + str(proc.pid)
    vars.task.pushRecovery(
                task.Recovery(name, "volume", "Volume", "killProcRollback",
                              [str(proc.pid),
                               str(misc.getProcCtime(proc.pid))]))


def qemuRebase(src, srcFormat, backingFile,
               backingFormat, unsafe, stop, rollback):
    """
    Rebase the 'src' volume on top of the new 'backingFile'
    with new 'backingFormat'
    """
    backingFormat = fmt2str(backingFormat)
    srcFormat = fmt2str(srcFormat)
    cwd = os.path.dirname(src)
    log.debug('(qemuRebase): REBASE %s (fmt=%s) on top of %s (%s) START' %
              (src, srcFormat, backingFile, backingFormat))

    cmd = constants.CMD_LOWPRIO + [constants.EXT_QEMUIMG, "rebase",
                                   "-t", "none", "-f", srcFormat,
                                   "-b", backingFile, "-F", backingFormat]
    if unsafe:
        cmd += ["-u"]
    cmd += [src]

    recoveryCallback = None
    if rollback:
        recoveryCallback = baseAsyncTasksRollback
    (rc, out, err) = misc.watchCmd(cmd, stop=stop, cwd=cwd,
                                   recoveryCallback=recoveryCallback)

    log.debug('(qemuRebase): REBASE %s DONE' % (src))
    return (rc, out, err)


def qemuConvert(src, dst, src_fmt, dst_fmt, stop, size, dstvolType):
    """
    Convert the 'src' image (or chain of images) into a new single 'dst'
    """
    src_fmt = fmt2str(src_fmt)
    dst_fmt = fmt2str(dst_fmt)
    log.debug('(qemuConvert): COPY %s (%s) to %s (%s) START' %
              (src, src_fmt, dst, dst_fmt))

    if (src_fmt == "raw" and dst_fmt == "raw" and
        dstvolType == PREALLOCATED_VOL):
        (rc, out, err) = misc.ddWatchCopy(
                                 src=src, dst=dst,
                                 stop=stop, size=size,
                                 recoveryCallback=baseAsyncTasksRollback)
    else:
        cmd = constants.CMD_LOWPRIO + [constants.EXT_QEMUIMG, "convert",
                                       "-t", "none", "-f", src_fmt, src,
                                       "-O", dst_fmt, dst]
        (rc, out, err) = misc.watchCmd(cmd, stop=stop,
                                       recoveryCallback=baseAsyncTasksRollback)

    log.debug('(qemuConvert): COPY %s to %s DONE' % (src, dst))
    return (rc, out, err)
