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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#

import os
import uuid
import threading
import sanlock

from vdsm.config import config
import storage_exception as se
import volume
import image
import sd
import misc
from misc import logskip
from misc import deprecated
import task
import lvm
import resourceManager as rm
from threadLocal import vars
from sdc import sdCache
from resourceFactories import LVM_ACTIVATION_NAMESPACE
import fileUtils

TAG_PREFIX_MD = "MD_"
TAG_PREFIX_MDNUMBLKS = "MS_"
TAG_PREFIX_IMAGE = "IU_"
TAG_PREFIX_PARENT = "PU_"
VOLUME_TAGS = [TAG_PREFIX_PARENT,
               TAG_PREFIX_IMAGE,
               TAG_PREFIX_MD,
               TAG_PREFIX_MDNUMBLKS]


# volume meta data block size
VOLUME_METASIZE = 512
VOLUME_MDNUMBLKS = 1

# Reserved leases for special purposes:
#  - 0       SPM (Backward comapatibility with V0 and V2)
#  - 1       SDM (SANLock V3)
#  - 2..100  (Unassigned)
RESERVED_LEASES = 100

rmanager = rm.ResourceManager.getInstance()


def _getDeviceSize(devPath):
    with open(devPath, "rb") as f:
        f.seek(0, 2)
        return f.tell()


class BlockVolume(volume.Volume):
    """ Actually represents a single volume (i.e. part of virtual disk).
    """
    _tagCreateLock = threading.Lock()

    def __init__(self, repoPath, sdUUID, imgUUID, volUUID):
        self.metaoff = None
        volume.Volume.__init__(self, repoPath, sdUUID, imgUUID, volUUID)
        self.lvmActivationNamespace = sd.getNamespace(self.sdUUID,
                                                      LVM_ACTIVATION_NAMESPACE)

    def validate(self):
        try:
            lvm.getLV(self.sdUUID, self.volUUID)
        except se.LogicalVolumeDoesNotExistError:
            raise se.VolumeDoesNotExist(self.volUUID)  # Fix me
        volume.Volume.validate(self)

    def refreshVolume(self):
        lvm.refreshLV(self.sdUUID, self.volUUID)

    @classmethod
    def getVSize(cls, sdobj, imgUUID, volUUID, bs=512):
        try:
            return _getDeviceSize(lvm.lvPath(sdobj.sdUUID, volUUID)) / bs
        except Exception, e:
            # The volume might not be active, skip logging.
            if not (isinstance(e, IOError) and e.errno == os.errno.ENOENT):
                cls.log.warn("Could not get size for vol %s/%s using "
                    "optimized methods", sdobj.sdUUID, volUUID, exc_info=True)

        # Fallback to the traditional way.
        return int(int(lvm.getLV(sdobj.sdUUID, volUUID).size) / bs)

    getVTrueSize = getVSize

    @classmethod
    def halfbakedVolumeRollback(cls, taskObj, sdUUID, volUUID, volPath):
        cls.log.info("sdUUID=%s volUUID=%s volPath=%s" %
                      (sdUUID, volUUID, volPath))

        try:
            # Fix me: assert resource lock.
            lvm.getLV(sdUUID, volUUID)
            lvm.removeLVs(sdUUID, volUUID)
        except se.LogicalVolumeDoesNotExistError, e:
            pass  # It's OK: inexistent LV, don't try to remove.
        except se.CannotRemoveLogicalVolume, e:
            cls.log.warning("Remove logical volume failed %s/%s %s", sdUUID,
                             volUUID, str(e))

        if os.path.lexists(volPath):
            os.unlink(volPath)

    @classmethod
    def validateCreateVolumeParams(cls, volFormat, preallocate, srcVolUUID):
        """
        Validate create volume parameters.
        'srcVolUUID' - backing volume UUID
        'volFormat' - volume format RAW/QCOW2
        'preallocate' - sparse/preallocate
        """
        volume.Volume.validateCreateVolumeParams(volFormat, preallocate,
                                                 srcVolUUID)

        # Sparse-Raw not supported for block volumes
        if preallocate == volume.SPARSE_VOL and volFormat == volume.RAW_FORMAT:
            raise se.IncorrectFormat(srcVolUUID)

        # Snapshot should be COW volume
        if srcVolUUID != volume.BLANK_UUID and volFormat != volume.COW_FORMAT:
            raise se.IncorrectFormat(srcVolUUID)

    @classmethod
    def createVolumeMetadataRollback(cls, taskObj, sdUUID, offs):
        cls.log.info("createVolumeMetadataRollback: sdUUID=%s offs=%s" %
                      (sdUUID, offs))
        metaid = [sdUUID, int(offs)]
        cls.__putMetadata({"NONE": "#" * (sd.METASIZE - 10)}, metaid)

    @classmethod
    def create(cls, repoPath, sdUUID, imgUUID, size, volFormat, preallocate,
               diskType, volUUID, desc, srcImgUUID, srcVolUUID):
        """
       Create a new volume with given size or snapshot
            'size' - in sectors
            'volFormat' - volume format COW / RAW
            'preallocate' - Preallocate / Sparse
            'diskType' - string that describes disk type
                         System|Data|Shared|Swap|Temp
            'srcImgUUID' - source image UUID
            'srcVolUUID' - source volume UUID
        """
        if not volUUID:
            volUUID = str(uuid.uuid4())
        if volUUID == volume.BLANK_UUID:
            raise se.InvalidParameterException("volUUID", volUUID)

        # Validate volume parameters should be checked here for all
        # internal flows using volume creation.
        cls.validateCreateVolumeParams(volFormat, preallocate, srcVolUUID)

        mysd = sdCache.produce(sdUUID=sdUUID)
        try:
            lvm.getLV(sdUUID, volUUID)
        except se.LogicalVolumeDoesNotExistError:
            pass  # OK, this is a new volume
        else:
            raise se.VolumeAlreadyExists(volUUID)

        imageDir = image.Image(repoPath).create(sdUUID, imgUUID)
        vol_path = os.path.join(imageDir, volUUID)
        pvol = None
        voltype = "LEAF"

        try:
            if srcVolUUID != volume.BLANK_UUID:
                # We have a parent
                if srcImgUUID == volume.BLANK_UUID:
                    srcImgUUID = imgUUID
                pvol = BlockVolume(repoPath, sdUUID, srcImgUUID, srcVolUUID)
                # Cannot create snapshot for ILLEGAL volume
                if not pvol.isLegal():
                    raise se.createIllegalVolumeSnapshotError(pvol.volUUID)

                if imgUUID != srcImgUUID:
                    pvol.share(imageDir, hard=False)
                    pvol = BlockVolume(repoPath, sdUUID, imgUUID, srcVolUUID)

                # override size param by parent's size
                size = pvol.getSize()
        except se.StorageException:
            cls.log.error("Unexpected error", exc_info=True)
            raise
        except Exception, e:
            cls.log.error("Unexpected error", exc_info=True)
            raise se.VolumeCannotGetParent("blockVolume can't get parent %s "
                                           "for volume %s: %s" %
                                            (srcVolUUID, volUUID, str(e)))

        try:
            cls.log.info("blockVolume: creating LV: volUUID %s" % (volUUID))
            if preallocate == volume.SPARSE_VOL:
                volsize = "%s" % config.get("irs",
                                            "volume_utilization_chunk_mb")
            else:
                # should stay %d and size should be int(size)
                volsize = "%s" % (size / 2 / 1024)

            # Rollback sentinel, just to mark the start of the task
            vars.task.pushRecovery(task.Recovery(task.ROLLBACK_SENTINEL,
                                   "blockVolume", "BlockVolume",
                                   "startCreateVolumeRollback",
                                   [sdUUID, imgUUID, volUUID]))

            # create volume rollback
            vars.task.pushRecovery(task.Recovery("halfbaked volume rollback",
                                   "blockVolume", "BlockVolume",
                                   "halfbakedVolumeRollback",
                                   [sdUUID, volUUID, vol_path]))

            lvm.createLV(sdUUID, volUUID, volsize, activate=True)
            if os.path.exists(vol_path):
                os.unlink(vol_path)
            os.symlink(lvm.lvPath(sdUUID, volUUID), vol_path)
        except se.StorageException:
            cls.log.error("Unexpected error", exc_info=True)
            raise
        except Exception, e:
            cls.log.error("Unexpected error", exc_info=True)
            raise se.VolumeCreationError("blockVolume create/link lv %s "
                                         "failed: %s" % (volUUID, str(e)))

        # By definition volume is now a leaf and should be writeable.
        # Default permission for lvcreate is read and write.
        # No need to set permission.

        try:
            cls.log.info("blockVolume: create: volUUID %s srcImg %s srvVol %s"
                         % (volUUID, srcImgUUID, srcVolUUID))
            if not pvol:
                cls.log.info("Request to create %s volume %s with size = %s "
                             "sectors", volume.type2name(volFormat), vol_path,
                              size)

                # Create 'raw' volume via qemu-img actually redundant
                if volFormat == volume.COW_FORMAT:
                    volume.createVolume(None, None, vol_path, size, volFormat,
                                        preallocate)
            else:
                ## Create hardlink to template and its meta file
                cls.log.info("Request to create snapshot %s/%s of volume"
                             " %s/%s", imgUUID, volUUID, srcImgUUID,
                              srcVolUUID)

                pvol.clone(imageDir, volUUID, volFormat, preallocate)
        except Exception:
            cls.log.error("Unexpected error", exc_info=True)
            raise

        try:
            with cls._tagCreateLock:
                offs = mysd.mapMetaOffset(volUUID, VOLUME_MDNUMBLKS)
                lvm.addLVTags(sdUUID, volUUID, ("%s%s" % (TAG_PREFIX_MD, offs),
                              "%s%s" % (TAG_PREFIX_PARENT, srcVolUUID,),
                              "%s%s" % (TAG_PREFIX_IMAGE, imgUUID,)))

            vars.task.pushRecovery(task.Recovery("create block volume metadata"
                                   " rollback", "blockVolume", "BlockVolume",
                                   "createVolumeMetadataRollback",
                                   [sdUUID, str(offs)]))

            # Set metadata and mark volume as legal.
            # FIXME: In next version we should remove imgUUID and srcVolUUID,
            #        as they are saved on lvm tags
            cls.newMetadata([sdUUID, offs], sdUUID, imgUUID, srcVolUUID,
                            size, volume.type2name(volFormat),
                            volume.type2name(preallocate), voltype,
                            diskType, desc, volume.LEGAL_VOL)
            cls.newVolumeLease(sdUUID, volUUID, offs)
        except se.StorageException:
            cls.log.error("Unexpected error", exc_info=True)
            raise
        except Exception, e:
            cls.log.error("Unexpected error", exc_info=True)
            raise se.VolumeMetadataWriteError("tag target volume %s failed: %s"
                                               % (volUUID, str(e)))

        try:
            lvm.deactivateLVs(sdUUID, volUUID)
        except Exception:
            cls.log.warn("Cannot deactivate new created volume %s/%s", sdUUID,
                          volUUID, exc_info=True)

        # Remove all previous rollbacks for 'halfbaked' volume and add rollback
        # for 'real' volume creation
        vars.task.replaceRecoveries(task.Recovery("create block volume "
                                    "rollback", "blockVolume", "BlockVolume",
                                    "createVolumeRollback",
                                    [repoPath, sdUUID, imgUUID, volUUID,
                                     imageDir]))

        return volUUID

    def delete(self, postZero, force):
        """ Delete volume
            'postZero' - zeroing file before deletion
            'force' is required to remove shared and internal volumes
        """
        self.log.info("Request to delete LV %s of image %s in VG %s ",
                      self.volUUID, self.imgUUID, self.sdUUID)

        vol_path = self.getVolumePath()
        size = self.getVolumeSize(bs=1)
        offs = self.getMetaOffset()

        if not force:
            self.validateDelete()

        # Mark volume as illegal before deleting
        self.setLegality(volume.ILLEGAL_VOL)

        if postZero:
            self.prepare(justme=True, rw=True, chainrw=force, setrw=True,
                         force=True)
            try:
                misc.ddWatchCopy("/dev/zero", vol_path, vars.task.aborting,
                     int(size), recoveryCallback=volume.baseAsyncTasksRollback)
            except se.ActionStopped, e:
                raise e
            except Exception, e:
                self.log.error("Unexpected error", exc_info=True)
                raise se.VolumesZeroingError(vol_path)
            finally:
                self.teardown(self.sdUUID, self.volUUID, justme=True)

        # try to cleanup as much as possible
        eFound = se.CannotDeleteVolume(self.volUUID)
        puuid = None
        try:
            # We need to blank parent record in our metadata
            # for parent to become leaf successfully.
            puuid = self.getParent()
            self.setParent(volume.BLANK_UUID)
            if puuid and puuid != volume.BLANK_UUID:
                pvol = BlockVolume(self.repoPath, self.sdUUID, self.imgUUID,
                                   puuid)
                pvol.recheckIfLeaf()
        except Exception, e:
            eFound = e
            self.log.warning("cannot finalize parent volume %s", puuid,
                              exc_info=True)

        try:
            try:
                lvm.removeLVs(self.sdUUID, self.volUUID)
            except se.CannotRemoveLogicalVolume:
                # At this point LV is already marked as illegal, we will
                # try to cleanup whatever we can...
                pass

            self.removeMetadata([self.sdUUID, offs])
        except Exception, e:
            eFound = e
            self.log.error("cannot remove volume %s/%s", self.sdUUID,
                            self.volUUID, exc_info=True)

        try:
            os.unlink(vol_path)
            return True
        except Exception, e:
            eFound = e
            self.log.error("cannot delete volume's %s/%s link path: %s",
                            self.sdUUID, self.volUUID, vol_path, exc_info=True)

        raise eFound

    def extend(self, newSize):
        """Extend a logical volume
            'newSize' - new size in blocks
        """
        self.log.info("Request to extend LV %s of image %s in VG %s with "
                      "size = %s", self.volUUID, self.imgUUID, self.sdUUID,
                       newSize)
        # we should return: Success/Failure
        # Backend APIs:
        sizemb = (newSize + 2047) / 2048
        lvm.extendLV(self.sdUUID, self.volUUID, sizemb)

    @classmethod
    def renameVolumeRollback(cls, taskObj, sdUUID, oldUUID, newUUID):
        try:
            cls.log.info("renameVolumeRollback: sdUUID=%s oldUUID=%s "
                         "newUUID=%s", sdUUID, oldUUID, newUUID)
            lvm.renameLV(sdUUID, oldUUID, newUUID)
        except Exception:
            cls.log.error("Failure in renameVolumeRollback: sdUUID=%s "
                          "oldUUID=%s newUUID=%s", sdUUID, oldUUID, newUUID,
                           exc_info=True)

    def rename(self, newUUID, recovery=True):
        """
        Rename volume
        """
        self.log.info("Rename volume %s as %s ", self.volUUID, newUUID)
        if not self.imagePath:
            self.validateImagePath()

        if os.path.lexists(self.getVolumePath()):
            os.unlink(self.getVolumePath())

        if recovery:
            name = "Rename volume rollback: " + newUUID
            vars.task.pushRecovery(task.Recovery(name, "blockVolume",
                                   "BlockVolume", "renameVolumeRollback",
                                   [self.sdUUID, newUUID, self.volUUID]))

        lvm.renameLV(self.sdUUID, self.volUUID, newUUID)
        self.volUUID = newUUID
        self.volumePath = os.path.join(self.imagePath, newUUID)

    def getDevPath(self):
        """
        Return the underlying device (for sharing)
        """
        return lvm.lvPath(self.sdUUID, self.volUUID)

    def _share(self, dstImgPath):
        """
        Share this volume to dstImgPath
        """
        dstPath = os.path.join(dstImgPath, self.volUUID)

        self.log.debug("Share volume %s to %s", self.volUUID, dstImgPath)
        os.symlink(self.getDevPath(), dstPath)

    @classmethod
    def shareVolumeRollback(cls, taskObj, volPath):
        cls.log.info("Volume rollback for volPath=%s", volPath)

        try:
            fileUtils.safeUnlink(volPath)

        except Exception:
            cls.log.error("Unexpected error", exc_info=True)

    @deprecated  # valid only for domain version < 3, see volume.setrw
    def _setrw(self, rw):
        """
        Set the read/write permission on the volume (deprecated)
        """
        lvm.setrwLV(self.sdUUID, self.volUUID, rw)

    @logskip("ResourceManager")
    def llPrepare(self, rw=False, setrw=False):
        """
        Perform low level volume use preparation

        For the Block Volumes the actual LV activation is wrapped
        into lvmActivation resource. It is being initialized by the
        storage domain sitting on top of the encapsulating VG.
        We just use it here.
        """
        if setrw:
            self.setrw(rw=rw)
        access = rm.LockType.exclusive if rw else rm.LockType.shared
        activation = rmanager.acquireResource(self.lvmActivationNamespace,
                                              self.volUUID, access)
        activation.autoRelease = False

    @classmethod
    def teardown(cls, sdUUID, volUUID, justme=False):
        """
        Deactivate volume and release resources.
        Volume deactivation occurs as part of resource releasing.
        If justme is false, the entire COW chain should be torn down.
        """
        cls.log.info("Tearing down volume %s/%s justme %s"
                      % (sdUUID, volUUID, justme))
        lvmActivationNamespace = sd.getNamespace(sdUUID,
                                                 LVM_ACTIVATION_NAMESPACE)
        rmanager.releaseResource(lvmActivationNamespace, volUUID)
        if not justme:
            try:
                pvolUUID = _getVolumeTag(sdUUID, volUUID, TAG_PREFIX_PARENT)
            except Exception, e:
                # If storage not accessible or lvm error occurred
                # we will failure to get the parent volume.
                # We can live with it and still succeed in volume's teardown.
                pvolUUID = volume.BLANK_UUID
                cls.log.warn("Failure to get parent of volume %s/%s (%s)"
                              % (sdUUID, volUUID, e))

            if pvolUUID != volume.BLANK_UUID:
                cls.teardown(sdUUID=sdUUID, volUUID=pvolUUID, justme=False)

    def validateImagePath(self):
        """
        Block SD supports lazy image dir creation
        """
        imageDir = image.Image(self.repoPath).getImageDir(self.sdUUID,
                                                          self.imgUUID)
        if not os.path.isdir(imageDir):
            try:
                os.mkdir(imageDir, 0755)
            except Exception:
                self.log.error("Unexpected error", exc_info=True)
                raise se.ImagePathError(imageDir)
        self.imagePath = imageDir

    def validateVolumePath(self):
        """
        Block SD supports lazy volume link creation. Note that the volume can
        be still inactive.
        An explicit prepare is required to validate that the volume is active.
        """
        if not self.imagePath:
            self.validateImagePath()
        volPath = os.path.join(self.imagePath, self.volUUID)
        if not os.path.lexists(volPath):
            os.symlink(lvm.lvPath(self.sdUUID, self.volUUID), volPath)
        self.volumePath = volPath

    def findImagesByVolume(self, legal=False):
        """
        Find the image(s) UUID by one of its volume UUID.
        Templated and shared disks volumes may result more then one image.
        """
        lvs = lvm.getLV(self.sdUUID)
        imgUUIDs = [self.imgUUID]  # Add volume image
        for lv in lvs:
            imgUUID = ""
            parent = ""
            for tag in lv.tags:
                if tag.startswith(TAG_PREFIX_IMAGE):
                    imgUUID = tag[len(TAG_PREFIX_IMAGE):]
                elif tag.startswith(TAG_PREFIX_PARENT):
                    if tag[len(TAG_PREFIX_PARENT):] != self.volUUID:
                        break  # Not a child
                    parent = tag[len(TAG_PREFIX_PARENT):]
                if parent and image:
                    if imgUUID not in imgUUIDs:
                        imgUUIDs.append(imgUUID)
                    break

        # Check image legality, if needed
        if legal:
            for imgUUID in imgUUIDs[:]:
                if not image.Image(self.repoPath).isLegal(self.sdUUID,
                                                          imgUUID):
                    imgUUIDs.remove(imgUUID)

        return imgUUIDs

    def getVolumeTag(self, tagPrefix):
        return _getVolumeTag(self.sdUUID, self.volUUID, tagPrefix)

    def changeVolumeTag(self, tagPrefix, uuid):

        if tagPrefix not in VOLUME_TAGS:
            raise se.LogicalVolumeWrongTagError(tagPrefix)

        oldTag = ""
        for tag in lvm.getLV(self.sdUUID, self.volUUID).tags:
            if tag.startswith(tagPrefix):
                oldTag = tag
                break

        if not oldTag:
            raise se.MissingTagOnLogicalVolume(self.volUUID, tagPrefix)

        newTag = tagPrefix + uuid
        if oldTag != newTag:
            lvm.replaceLVTag(self.sdUUID, self.volUUID, oldTag, newTag)

    def getParent(self):
        """
        Return parent volume UUID
        """
        return self.getVolumeTag(TAG_PREFIX_PARENT)

    def getImage(self):
        """
        Return image UUID
        """
        return self.getVolumeTag(TAG_PREFIX_IMAGE)

    def setParent(self, puuid):
        """
        Set parent volume UUID
        """
        self.changeVolumeTag(TAG_PREFIX_PARENT, puuid)
        # FIXME In next version we should remove PUUID, as it is saved on lvm
        # tags
        self.setMetaParam(volume.PUUID, puuid)

    def setImage(self, imgUUID):
        """
        Set image UUID
        """
        self.changeVolumeTag(TAG_PREFIX_IMAGE, imgUUID)
        # FIXME In next version we should remove imgUUID, as it is saved on lvm
        # tags
        self.setMetaParam(volume.IMAGE, imgUUID)

    @classmethod
    def getImageVolumes(cls, repoPath, sdUUID, imgUUID):
        """
        Fetch the list of the Volumes UUIDs, not including the shared base
        (template)
        """
        lvs = lvm.lvsByTag(sdUUID, "%s%s" % (TAG_PREFIX_IMAGE, imgUUID))
        return [lv.name for lv in lvs]

    def removeMetadata(self, metaid):
        """
        Just wipe meta.
        """
        try:
            self.__putMetadata({"NONE": "#" * (sd.METASIZE - 10)}, metaid)
        except Exception, e:
            self.log.error(e, exc_info=True)
            raise se.VolumeMetadataWriteError(str(metaid) + str(e))

    @classmethod
    def __putMetadata(cls, meta, metaid):
        vgname = metaid[0]
        offs = metaid[1]
        lines = ["%s=%s\n" % (key.strip(), str(value).strip())
                 for key, value in meta.iteritems()]
        lines.append("EOF\n")
        metavol = lvm.lvPath(vgname, sd.METADATA)
        with fileUtils.DirectFile(metavol, "r+d") as f:
            data = "".join(lines)
            if len(data) > VOLUME_METASIZE:
                cls.log.warn("Truncating volume metadata (%s)", data)
                data = data[:VOLUME_METASIZE]
            else:
                data += "\0" * (VOLUME_METASIZE - len(data))

            f.seek(offs * VOLUME_METASIZE)
            f.write(data)

    @classmethod
    def createMetadata(cls, meta, metaid):
        cls.__putMetadata(meta, metaid)

    def getMetaOffset(self):
        if self.metaoff:
            return self.metaoff
        l = lvm.getLV(self.sdUUID, self.volUUID).tags
        for t in l:
            if t.startswith(TAG_PREFIX_MD):
                return int(t[3:])
        self.log.error("missing offset tag on volume %s", self.volUUID)
        raise se.VolumeMetadataReadError("missing offset tag on volume %s"
                                          % self.volUUID)

    def getMetadata(self, metaid=None):
        """
        Get Meta data array of key,values lines
        """
        if not metaid:
            vgname = self.sdUUID
            offs = self.getMetaOffset()
        else:
            vgname = metaid[0]
            offs = metaid[1]
        try:
            meta = misc.readblock(lvm.lvPath(vgname, sd.METADATA),
                offs * VOLUME_METASIZE, VOLUME_METASIZE)
            out = {}
            for l in meta:
                if l.startswith("EOF"):
                    return out
                if l.find("=") < 0:
                    continue
                key, value = l.split("=")
                out[key.strip()] = value.strip()
        except Exception, e:
            self.log.error(e, exc_info=True)
            raise se.VolumeMetadataReadError(str(metaid) + ":" + str(e))
        return out

    def setMetadata(self, metaarr, metaid=None):
        """
        Set the meta data hash as the new meta data of the Volume
        """
        if not metaid:
            metaid = [self.sdUUID, self.getMetaOffset()]
        try:
            self.__putMetadata(metaarr, metaid)
        except Exception, e:
            self.log.error(e, exc_info=True)
            raise se.VolumeMetadataWriteError(str(metaid) + str(e))

    @classmethod
    def newVolumeLease(cls, sdUUID, volUUID, leaseSlot):
        dom = sdCache.produce(sdUUID)

        if dom.hasVolumeLeases():
            leasePath = dom.getLeasesFilePath()
            leaseOffset = ((leaseSlot + RESERVED_LEASES)
                            * dom.logBlkSize * sd.LEASE_BLOCKS)
            sanlock.init_resource(sdUUID, volUUID, [(leasePath, leaseOffset)])

    def getVolumeSize(self, bs=512):
        """
        Return the volume size in blocks
        """
        # Just call the class method getVSize() - apparently it does what
        # we need. We consider incurred overhead of producing the SD object
        # to be a small price for code de-duplication.
        sdobj = sdCache.produce(sdUUID=self.sdUUID)
        return self.getVSize(sdobj, self.imgUUID, self.volUUID, bs)

    getVolumeTrueSize = getVolumeSize

    def getVolumeMtime(self):
        """
        Return the volume mtime in msec epoch
        """
        try:
            mtime = self.getMetaParam(volume.MTIME)
        except se.MetaDataKeyNotFoundError:
            mtime = 0

        return mtime


def _getVolumeTag(sdUUID, volUUID, tagPrefix):
    for tag in lvm.getLV(sdUUID, volUUID).tags:
        if tag.startswith(tagPrefix):
            return tag[len(tagPrefix):]

    raise se.MissingTagOnLogicalVolume(volUUID, tagPrefix)


def _postZero(sdUUID, volumes):
    # Assumed that there is no any thread that can deactivate these LVs
    # on this host or change the rw permission on this or any other host.

    lvNames = tuple(vol.volUUID for vol in volumes)
    #Assert volumes are writable. (Don't do this at home.)
    try:
        lvm.changelv(sdUUID, lvNames, "--permission", "rw")
    except se.StorageException, e:
        # Hope this only means that some volumes were already writable.
        pass

    lvm.activateLVs(sdUUID, lvNames)

    for lv in lvm.getLV(sdUUID):
        if lv.name in lvNames:
            # wipe out the whole volume
            try:
                misc.ddWatchCopy("/dev/zero", lvm.lvPath(sdUUID, lv.name),
                                vars.task.aborting, int(lv.size),
                                recoveryCallback=volume.baseAsyncTasksRollback)
            except se.ActionStopped, e:
                raise e
            except Exception, e:
                raise se.VolumesZeroingError(lv.name)


def deleteMultipleVolumes(sdUUID, volumes, postZero):
    "Delete multiple volumes (LVs) in the same domain (VG)."""
    if postZero:
        _postZero(sdUUID, volumes)
    lvNames = [vol.volUUID for vol in volumes]
    lvm.removeLVs(sdUUID, lvNames)
