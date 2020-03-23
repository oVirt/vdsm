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
from contextlib import contextmanager

from vdsm import utils
from vdsm.config import config
from vdsm.common import cmdutils
from vdsm.common import logutils
from vdsm.common.marks import deprecated
from vdsm.common.threadlocal import vars
from vdsm.common.units import MiB
from vdsm.storage import constants as sc
from vdsm.storage import exception as se
from vdsm.storage import imageSharing
from vdsm.storage import qemuimg
from vdsm.storage import resourceManager as rm
from vdsm.storage import sd
from vdsm.storage import volume
from vdsm.storage import workarounds
from vdsm.storage.sdc import sdCache

from vdsm.common.exception import ActionStopped

log = logging.getLogger('storage.Image')

# What volumes to synchronize
SYNC_VOLUMES_ALL = 'ALL'
SYNC_VOLUMES_INTERNAL = 'INTERNAL'
SYNC_VOLUMES_LEAF = 'LEAF'

# Image Operations
UNKNOWN_OP = 0
COPY_OP = 1
MOVE_OP = 2
OP_TYPES = {UNKNOWN_OP: 'UNKNOWN', COPY_OP: 'COPY', MOVE_OP: 'MOVE'}

RENAME_RANDOM_STRING_LEN = 8


def _deleteImage(dom, imgUUID, postZero, discard):
    """This ancillary function will be removed.

    Replaces Image.delete() in Image.[copyCollapsed(), move(), multimove()].
    """
    allVols = dom.getAllVolumes()
    imgVols = sd.getVolsOfImage(allVols, imgUUID)
    if not imgVols:
        log.warning("No volumes found for image %s. %s", imgUUID, allVols)
        return
    elif postZero:
        dom.zeroImage(dom.sdUUID, imgUUID, imgVols, discard)
    else:
        dom.deleteImage(dom.sdUUID, imgUUID, imgVols)


class Image:
    """ Actually represents a whole virtual disk.
        Consist from chain of volumes.
    """
    log = logging.getLogger('storage.Image')
    _fakeTemplateLock = threading.Lock()

    def __init__(self, repoPath):
        self._repoPath = repoPath

    @property
    def repoPath(self):
        return self._repoPath

    def _run_qemuimg_operation(self, operation):
        self.log.debug('running qemu-img operation')
        with vars.task.abort_callback(operation.abort):
            operation.run()
        self.log.debug('qemu-img operation has completed')

    def estimate_qcow2_size(self, src_vol_params, dst_sd_id):
        """
        Calculate volume allocation size for converting raw/qcow2
        source volume to qcow2 volume on destination storage domain.

        Arguments:
            src_vol_params(dict): Dictionary returned from
                                  `storage.volume.Volume.getVolumeParams()`
            dst_sd_id(str) : Destination volume storage domain id

        Returns:
            Volume allocation in bytes
        """
        # measure required size.
        qemu_measure = qemuimg.measure(
            image=src_vol_params['path'],
            format=sc.fmt2str(src_vol_params['volFormat']),
            output_format=qemuimg.FORMAT.QCOW2)

        # Adds extra room so we don't have to extend this disk immediately
        # when a vm is started.
        chunk_size_mb = config.getint("irs", "volume_utilization_chunk_mb")
        chunk_size = chunk_size_mb * MiB
        required = (qemu_measure["required"] + chunk_size)
        # Limit estimates size by maximum size.
        vol_class = sdCache.produce(dst_sd_id).getVolumeClass()
        max_size = vol_class.max_size(src_vol_params['capacity'],
                                      sc.COW_FORMAT)
        allocation = min(required, max_size)

        # Return estimated size of allocation.
        self.log.debug("Estimated allocation for qcow2 volume:"
                       "%d", allocation)
        return allocation

    def estimateChainSize(self, sdUUID, imgUUID, volUUID, capacity):
        """
        Compute an estimate of the whole chain size
        using the sum of the actual size of the chain's volumes
        """
        chain = self.getChain(sdUUID, imgUUID, volUUID)
        log_str = logutils.volume_chain_to_str(vol.volUUID for vol in chain)
        self.log.info("chain=%s ", log_str)

        chain_allocation = 0
        template = chain[0].getParentVolume()
        if template:
            chain_allocation = template.getVolumeSize()
        for vol in chain:
            chain_allocation += vol.getVolumeSize()
        if chain_allocation > capacity:
            chain_allocation = capacity
        # allocate %10 more for cow metadata
        chain_allocation = int(chain_allocation * sc.COW_OVERHEAD)
        return chain_allocation

    def getChain(self, sdUUID, imgUUID, volUUID=None):
        """
        Return the chain of volumes of image as a sorted list
        (not including a shared base (template) if any)
        """
        chain = []
        volclass = sdCache.produce(sdUUID).getVolumeClass()

        # Use volUUID when provided
        if volUUID:
            srcVol = volclass(self.repoPath, sdUUID, imgUUID, volUUID)

            # For template images include only one volume (the template itself)
            # NOTE: this relies on the fact that in a template there is only
            #       one volume
            if srcVol.isShared():
                return [srcVol]

        # Find all the volumes when volUUID is not provided
        else:
            # Find all volumes of image
            uuidlist = volclass.getImageVolumes(sdUUID, imgUUID)

            if not uuidlist:
                raise se.ImageDoesNotExistInSD(imgUUID, sdUUID)

            srcVol = volclass(self.repoPath, sdUUID, imgUUID, uuidlist[0])

            # For template images include only one volume (the template itself)
            if len(uuidlist) == 1 and srcVol.isShared():
                return [srcVol]

            # Searching for the leaf
            for vol in uuidlist:
                srcVol = volclass(self.repoPath, sdUUID, imgUUID, vol)

                if srcVol.isLeaf():
                    break

                srcVol = None

            if not srcVol:
                self.log.error("There is no leaf in the image %s", imgUUID)
                raise se.ImageIsNotLegalChain(imgUUID)

        # We have seen corrupted chains that cause endless loops here.
        # https://bugzilla.redhat.com/1125197
        seen = set()

        # Build up the sorted parent -> child chain
        while not srcVol.isShared():
            chain.insert(0, srcVol)
            seen.add(srcVol.volUUID)

            parentUUID = srcVol.getParent()
            if parentUUID == sc.BLANK_UUID:
                break

            if parentUUID in seen:
                self.log.error("Image %s volume %s has invalid parent UUID %s",
                               imgUUID, srcVol.volUUID, parentUUID)
                raise se.ImageIsNotLegalChain(imgUUID)

            srcVol = srcVol.getParentVolume()

        return chain

    def getTemplate(self, sdUUID, imgUUID):
        """
        Return template of the image
        """
        tmpl = None
        # Find all volumes of image (excluding template)
        chain = self.getChain(sdUUID, imgUUID)
        # check if the chain is build above a template, or it is a standalone
        pvol = chain[0].getParentVolume()
        if pvol:
            tmpl = pvol
        elif chain[0].isShared():
            tmpl = chain[0]

        return tmpl

    def createFakeTemplate(self, sdUUID, volParams):
        """
        Create fake template (relevant for Backup domain only)
        """
        with self._fakeTemplateLock:
            try:
                destDom = sdCache.produce(sdUUID)
                volclass = destDom.getVolumeClass()
                # Validate that the destination template exists and accessible
                volclass(self.repoPath, sdUUID, volParams['imgUUID'],
                         volParams['volUUID'])
            except (se.VolumeDoesNotExist, se.ImagePathError):
                try:
                    # Create fake parent volume
                    destDom.createVolume(
                        imgUUID=volParams['imgUUID'],
                        capacity=volParams['capacity'],
                        volFormat=sc.COW_FORMAT,
                        preallocate=sc.SPARSE_VOL,
                        diskType=volParams['disktype'],
                        volUUID=volParams['volUUID'], desc="Fake volume",
                        srcImgUUID=sc.BLANK_UUID,
                        srcVolUUID=sc.BLANK_UUID)

                    vol = destDom.produceVolume(imgUUID=volParams['imgUUID'],
                                                volUUID=volParams['volUUID'])
                    # Mark fake volume as "FAKE"
                    vol.setLegality(sc.FAKE_VOL)
                    # Mark fake volume as shared
                    vol.setShared()
                    # Now we should re-link all hardlinks of this template in
                    # all VMs based on it
                    destDom.templateRelink(volParams['imgUUID'],
                                           volParams['volUUID'])

                    self.log.debug("Succeeded to create fake image %s in "
                                   "domain %s", volParams['imgUUID'],
                                   destDom.sdUUID)
                except Exception:
                    self.log.error("Failure to create fake image %s in domain "
                                   "%s", volParams['imgUUID'], destDom.sdUUID,
                                   exc_info=True)

    def isLegal(self, sdUUID, imgUUID):
        """
        Check correctness of the whole chain (excluding template)
        """
        try:
            legal = True
            volclass = sdCache.produce(sdUUID).getVolumeClass()
            vollist = volclass.getImageVolumes(sdUUID, imgUUID)
            self.log.info("image %s in domain %s has vollist %s", imgUUID,
                          sdUUID, str(vollist))
            for v in vollist:
                vol = volclass(self.repoPath, sdUUID, imgUUID, v)
                if not vol.isLegal() or vol.isFake():
                    legal = False
                    break
        except:
            legal = False
        return legal

    def __cleanupMove(self, srcVol, dstVol):
        """
        Cleanup environments after move operation
        """
        try:
            if srcVol:
                srcVol.teardown(sdUUID=srcVol.sdUUID, volUUID=srcVol.volUUID)
            if dstVol:
                dstVol.teardown(sdUUID=dstVol.sdUUID, volUUID=dstVol.volUUID)
        except Exception:
            self.log.error("Unexpected error", exc_info=True)

    def _createTargetImage(self, destDom, srcSdUUID, imgUUID):
        # Before actual data copying we need perform several operation
        # such as: create all volumes, create fake template if needed, ...
        try:
            # Find all volumes of source image
            srcChain = self.getChain(srcSdUUID, imgUUID)
            log_str = logutils.volume_chain_to_str(
                vol.volUUID for vol in srcChain)
            self.log.info("Source chain=%s ", log_str)
        except se.StorageException:
            self.log.error("Unexpected error", exc_info=True)
            raise
        except Exception as e:
            self.log.error("Unexpected error", exc_info=True)
            raise se.SourceImageActionError(imgUUID, srcSdUUID, str(e))

        fakeTemplate = False
        pimg = sc.BLANK_UUID    # standalone chain
        # check if the chain is build above a template, or it is a standalone
        pvol = srcChain[0].getParentVolume()
        if pvol:
            # find out parent volume parameters
            volParams = pvol.getVolumeParams()
            pimg = volParams['imgUUID']      # pimg == template image
            if destDom.isBackup():
                # FIXME: This workaround help as copy VM to the backup domain
                #        without its template. We will create fake template
                #        for future VM creation and mark it as FAKE volume.
                #        This situation is relevant for backup domain only.
                fakeTemplate = True

        @contextmanager
        def justLogIt(img):
            self.log.debug("You don't really need lock parent of image %s",
                           img)
            yield

        dstImageResourcesNamespace = rm.getNamespace(sc.IMAGE_NAMESPACE,
                                                     destDom.sdUUID)
        # In destination domain we need to lock image's template if exists
        with rm.acquireResource(dstImageResourcesNamespace, pimg, rm.SHARED) \
                if pimg != sc.BLANK_UUID else justLogIt(imgUUID):
            if fakeTemplate:
                self.createFakeTemplate(destDom.sdUUID, volParams)

            dstChain = []
            for srcVol in srcChain:
                # Create the dst volume
                try:
                    # find out src volume parameters
                    volParams = srcVol.getVolumeParams()

                    # To avoid prezeroing preallocated volumes on NFS domains
                    # we create the target as a sparse volume (since it will be
                    # soon filled with the data coming from the copy) and then
                    # we change its metadata back to the original value.
                    if (destDom.supportsSparseness or
                            volParams['volFormat'] != sc.RAW_FORMAT):
                        tmpVolPreallocation = sc.SPARSE_VOL
                    else:
                        tmpVolPreallocation = sc.PREALLOCATED_VOL

                    destDom.createVolume(
                        imgUUID=imgUUID,
                        capacity=volParams['capacity'],
                        volFormat=volParams['volFormat'],
                        preallocate=tmpVolPreallocation,
                        diskType=volParams['disktype'],
                        volUUID=srcVol.volUUID,
                        desc=volParams['descr'],
                        srcImgUUID=pimg,
                        srcVolUUID=volParams['parent'])

                    dstVol = destDom.produceVolume(imgUUID=imgUUID,
                                                   volUUID=srcVol.volUUID)

                    # Extend volume (for LV only) size to the actual size
                    dstVol.extend(volParams['apparentsize'])

                    # Change destination volume metadata to preallocated in
                    # case we've used a sparse volume to accelerate the
                    # volume creation
                    if volParams['prealloc'] == sc.PREALLOCATED_VOL \
                            and tmpVolPreallocation != sc.PREALLOCATED_VOL:
                        dstVol.setType(sc.PREALLOCATED_VOL)

                    dstChain.append(dstVol)
                except se.StorageException:
                    self.log.error("Unexpected error", exc_info=True)
                    raise
                except Exception as e:
                    self.log.error("Unexpected error", exc_info=True)
                    raise se.DestImageActionError(imgUUID, destDom.sdUUID,
                                                  str(e))

                # only base may have a different parent image
                pimg = imgUUID

        return {'srcChain': srcChain, 'dstChain': dstChain}

    def _interImagesCopy(self, destDom, srcSdUUID, imgUUID, chains):
        srcLeafVol = chains['srcChain'][-1]
        dstLeafVol = chains['dstChain'][-1]
        try:
            # Prepare the whole chains before the copy
            srcLeafVol.prepare(rw=False)
            dstLeafVol.prepare(rw=True, chainrw=True, setrw=True)
        except Exception:
            self.log.error("Unexpected error", exc_info=True)
            # teardown volumes
            self.__cleanupMove(srcLeafVol, dstLeafVol)
            raise

        try:
            for srcVol in chains['srcChain']:
                # Do the actual copy
                try:
                    dstVol = destDom.produceVolume(imgUUID=imgUUID,
                                                   volUUID=srcVol.volUUID)

                    if workarounds.invalid_vm_conf_disk(srcVol):
                        srcFormat = dstFormat = qemuimg.FORMAT.RAW
                    else:
                        srcFormat = sc.fmt2str(srcVol.getFormat())
                        dstFormat = sc.fmt2str(dstVol.getFormat())

                    parentVol = dstVol.getParentVolume()

                    if parentVol is not None:
                        backing = volume.getBackingVolumePath(
                            imgUUID, parentVol.volUUID)
                        backingFormat = sc.fmt2str(parentVol.getFormat())
                    else:
                        backing = None
                        backingFormat = None

                    if (destDom.supportsSparseness and
                            dstVol.getType() == sc.PREALLOCATED_VOL):
                        preallocation = qemuimg.PREALLOCATION.FALLOC
                    else:
                        preallocation = None

                    operation = qemuimg.convert(
                        srcVol.getVolumePath(),
                        dstVol.getVolumePath(),
                        srcFormat=srcFormat,
                        dstFormat=dstFormat,
                        dstQcow2Compat=destDom.qcow2_compat(),
                        backing=backing,
                        backingFormat=backingFormat,
                        preallocation=preallocation,
                        unordered_writes=destDom.recommends_unordered_writes(
                            dstVol.getFormat()),
                        create=(destDom.getStorageType() not in
                                sd.BLOCK_DOMAIN_TYPES)
                    )
                    with utils.stopwatch("Copy volume %s"
                                         % srcVol.volUUID):
                        self._run_qemuimg_operation(operation)
                except ActionStopped:
                    raise
                except se.StorageException:
                    self.log.error("Unexpected error", exc_info=True)
                    raise
                except Exception:
                    self.log.error("Copy image error: image=%s, src domain=%s,"
                                   " dst domain=%s", imgUUID, srcSdUUID,
                                   destDom.sdUUID, exc_info=True)
                    raise se.CopyImageError()
        finally:
            # teardown volumes
            self.__cleanupMove(srcLeafVol, dstLeafVol)

    def _finalizeDestinationImage(self, destDom, imgUUID, chains, force):
        for srcVol in chains['srcChain']:
            try:
                dstVol = destDom.produceVolume(imgUUID=imgUUID,
                                               volUUID=srcVol.volUUID)
                # In case of copying template, we should set the destination
                # volume as SHARED (after copy because otherwise prepare as RW
                # would fail)
                if srcVol.isShared():
                    dstVol.setShared()
                elif srcVol.isInternal():
                    dstVol.setInternal()
            except se.StorageException:
                self.log.error("Unexpected error", exc_info=True)
                raise
            except Exception as e:
                self.log.error("Unexpected error", exc_info=True)
                raise se.DestImageActionError(imgUUID, destDom.sdUUID, str(e))

    def move(self, srcSdUUID, dstSdUUID, imgUUID, vmUUID, op, postZero, force,
             discard):
        """
        Move/Copy image between storage domains within same storage pool
        """
        self.log.info("srcSdUUID=%s dstSdUUID=%s imgUUID=%s vmUUID=%s op=%s "
                      "force=%s postZero=%s discard=%s", srcSdUUID, dstSdUUID,
                      imgUUID, vmUUID, OP_TYPES[op], str(force), str(postZero),
                      discard)

        destDom = sdCache.produce(dstSdUUID)
        # If image already exists check whether it illegal/fake, overwrite it
        if not self.isLegal(destDom.sdUUID, imgUUID):
            force = True
        # We must first remove the previous instance of image (if exists)
        # in destination domain, if we got the overwrite command
        if force:
            self.log.info("delete image %s on domain %s before overwriting",
                          imgUUID, destDom.sdUUID)
            _deleteImage(destDom, imgUUID, postZero, discard)

        chains = self._createTargetImage(destDom, srcSdUUID, imgUUID)
        self._interImagesCopy(destDom, srcSdUUID, imgUUID, chains)
        self._finalizeDestinationImage(destDom, imgUUID, chains, force)
        if force:
            leafVol = chains['dstChain'][-1]
            # Now we should re-link all deleted hardlinks, if exists
            destDom.templateRelink(imgUUID, leafVol.volUUID)

        # At this point we successfully finished the 'copy' part of the
        # operation and we can clear all recoveries.
        vars.task.clearRecoveries()
        # If it's 'move' operation, we should delete src image after copying
        if op == MOVE_OP:
            # TODO: Should raise here.
            try:
                dom = sdCache.produce(srcSdUUID)
                _deleteImage(dom, imgUUID, postZero, discard)
            except se.StorageException:
                self.log.warning("Failed to remove img: %s from srcDom %s: "
                                 "after it was copied to: %s", imgUUID,
                                 srcSdUUID, dstSdUUID)

        self.log.info("%s task on image %s was successfully finished",
                      OP_TYPES[op], imgUUID)
        return True

    @deprecated
    def cloneStructure(self, sdUUID, imgUUID, dstSdUUID):
        self._createTargetImage(sdCache.produce(dstSdUUID), sdUUID, imgUUID)

    @deprecated
    def syncData(self, sdUUID, imgUUID, dstSdUUID, syncType):
        srcChain = self.getChain(sdUUID, imgUUID)
        log_str = logutils.volume_chain_to_str(vol.volUUID for vol in srcChain)
        self.log.info("Source chain=%s ", log_str)

        dstChain = self.getChain(dstSdUUID, imgUUID)
        log_str = logutils.volume_chain_to_str(vol.volUUID for vol in dstChain)
        self.log.info("Dest chain=%s ", log_str)

        if syncType == SYNC_VOLUMES_INTERNAL:
            try:
                # Removing the leaf volumes
                del srcChain[-1], dstChain[-1]
            except IndexError:
                raise se.ImageIsNotLegalChain()
        elif syncType == SYNC_VOLUMES_LEAF:
            try:
                # Removing all the internal volumes
                del srcChain[:-1], dstChain[:-1]
            except IndexError:
                raise se.ImageIsNotLegalChain()
        elif syncType != SYNC_VOLUMES_ALL:
            raise se.MiscNotImplementedException()

        if len(srcChain) != len(dstChain):
            raise se.DestImageActionError(imgUUID, dstSdUUID)

        # Checking the volume uuids (after removing the leaves to allow
        # different uuids for the current top layer, see previous check).
        for i, v in enumerate(srcChain):
            if v.volUUID != dstChain[i].volUUID:
                raise se.DestImageActionError(imgUUID, dstSdUUID)

        dstDom = sdCache.produce(dstSdUUID)

        self._interImagesCopy(dstDom, sdUUID, imgUUID,
                              {'srcChain': srcChain, 'dstChain': dstChain})
        self._finalizeDestinationImage(dstDom, imgUUID,
                                       {'srcChain': srcChain,
                                        'dstChain': dstChain}, False)

    def __cleanupCopy(self, srcVol, dstVol):
        """
        Cleanup environments after copy operation
        """
        try:
            if srcVol:
                srcVol.teardown(sdUUID=srcVol.sdUUID, volUUID=srcVol.volUUID)
            if dstVol:
                dstVol.teardown(sdUUID=dstVol.sdUUID, volUUID=dstVol.volUUID)
        except Exception:
            self.log.error("Unexpected error", exc_info=True)

    def validateVolumeChain(self, sdUUID, imgUUID):
        """
        Check correctness of the whole chain (including template if exists)
        """
        if not self.isLegal(sdUUID, imgUUID):
            raise se.ImageIsNotLegalChain(imgUUID)
        chain = self.getChain(sdUUID, imgUUID)
        log_str = logutils.volume_chain_to_str(vol.volUUID for vol in chain)
        self.log.info("Current chain=%s ", log_str)

        # check if the chain is build above a template, or it is a standalone
        pvol = chain[0].getParentVolume()
        if pvol:
            if not pvol.isLegal() or pvol.isFake():
                raise se.ImageIsNotLegalChain(imgUUID)

    def copyCollapsed(self, sdUUID, vmUUID, srcImgUUID, srcVolUUID, dstImgUUID,
                      dstVolUUID, descr, dstSdUUID, volType, volFormat,
                      preallocate, postZero, force, discard):
        """
        Create new template/volume from VM.
        Do it by collapse and copy the whole chain (baseVolUUID->srcVolUUID)
        """
        self.log.info("sdUUID=%s vmUUID=%s srcImgUUID=%s srcVolUUID=%s "
                      "dstImgUUID=%s dstVolUUID=%s dstSdUUID=%s volType=%s "
                      "volFormat=%s preallocate=%s force=%s postZero=%s "
                      "discard=%s",
                      sdUUID, vmUUID, srcImgUUID, srcVolUUID, dstImgUUID,
                      dstVolUUID, dstSdUUID, volType,
                      sc.type2name(volFormat), sc.type2name(preallocate),
                      str(force), str(postZero), discard)
        try:
            srcVol = dstVol = None

            # Find out dest sdUUID
            if dstSdUUID == sd.BLANK_UUID:
                dstSdUUID = sdUUID
            volclass = sdCache.produce(sdUUID).getVolumeClass()
            destDom = sdCache.produce(dstSdUUID)

            # find src volume
            try:
                srcVol = volclass(self.repoPath, sdUUID, srcImgUUID,
                                  srcVolUUID)
            except se.StorageException:
                raise
            except Exception as e:
                self.log.error(e, exc_info=True)
                raise se.SourceImageActionError(srcImgUUID, sdUUID, str(e))

            # Create dst volume
            try:
                # Before reading source volume parameters from volume metadata,
                # prepare the volume. This ensure that the volume capacity will
                # match the actual virtual size, see
                # https://bugzilla.redhat.com/1700623.
                srcVol.prepare(rw=False)

                volParams = srcVol.getVolumeParams()

                if volFormat in [sc.COW_FORMAT, sc.RAW_FORMAT]:
                    dstVolFormat = volFormat
                else:
                    dstVolFormat = volParams['volFormat']

                # TODO: This is needed only when copying to qcow2-thin volume
                # on block storage. Move into calculate_initial_size.
                dst_vol_allocation = self.calculate_vol_alloc(
                    sdUUID, volParams, dstSdUUID, dstVolFormat)

                # Find out dest volume parameters
                if preallocate in [sc.PREALLOCATED_VOL, sc.SPARSE_VOL]:
                    volParams['prealloc'] = preallocate

                initial_size = self.calculate_initial_size(
                    destDom.supportsSparseness,
                    dstVolFormat,
                    volParams['prealloc'],
                    dst_vol_allocation)

                self.log.info(
                    "Copy source %s:%s:%s to destination %s:%s:%s "
                    "capacity=%s, initial size=%s",
                    sdUUID,
                    srcImgUUID,
                    srcVolUUID,
                    dstSdUUID,
                    dstImgUUID,
                    dstVolUUID,
                    volParams['capacity'],
                    initial_size)

                # If image already exists check whether it illegal/fake,
                # overwrite it
                if not self.isLegal(dstSdUUID, dstImgUUID):
                    force = True

                # We must first remove the previous instance of image (if
                # exists) in destination domain, if we got the overwrite
                # command
                if force:
                    self.log.info("delete image %s on domain %s before "
                                  "overwriting", dstImgUUID, dstSdUUID)
                    _deleteImage(destDom, dstImgUUID, postZero, discard)

                destDom.createVolume(
                    imgUUID=dstImgUUID,
                    capacity=volParams['capacity'],
                    volFormat=dstVolFormat,
                    preallocate=volParams['prealloc'],
                    diskType=volParams['disktype'],
                    volUUID=dstVolUUID,
                    desc=descr,
                    srcImgUUID=sc.BLANK_UUID,
                    srcVolUUID=sc.BLANK_UUID,
                    initial_size=initial_size)

                dstVol = sdCache.produce(dstSdUUID).produceVolume(
                    imgUUID=dstImgUUID, volUUID=dstVolUUID)

            except se.StorageException:
                self.log.error("Unexpected error", exc_info=True)
                raise
            except Exception as e:
                self.log.error("Unexpected error", exc_info=True)
                raise se.CopyImageError("Destination volume %s error: %s" %
                                        (dstVolUUID, str(e)))

            try:
                # Start the actual copy image procedure
                dstVol.prepare(rw=True, setrw=True)

                if (destDom.supportsSparseness and
                        dstVol.getType() == sc.PREALLOCATED_VOL):
                    preallocation = qemuimg.PREALLOCATION.FALLOC
                else:
                    preallocation = None

                try:
                    operation = qemuimg.convert(
                        volParams['path'],
                        dstVol.getVolumePath(),
                        srcFormat=sc.fmt2str(volParams['volFormat']),
                        dstFormat=sc.fmt2str(dstVolFormat),
                        dstQcow2Compat=destDom.qcow2_compat(),
                        preallocation=preallocation,
                        unordered_writes=destDom.recommends_unordered_writes(
                            dstVolFormat),
                        create=(destDom.getStorageType() not in
                                sd.BLOCK_DOMAIN_TYPES)
                    )
                    with utils.stopwatch("Copy volume %s"
                                         % srcVol.volUUID):
                        self._run_qemuimg_operation(operation)
                except ActionStopped:
                    raise
                except cmdutils.Error as e:
                    self.log.exception('conversion failure for volume %s',
                                       srcVol.volUUID)
                    raise se.CopyImageError(str(e))

                # Mark volume as SHARED
                if volType == sc.SHARED_VOL:
                    dstVol.setShared()

                dstVol.setLegality(sc.LEGAL_VOL)

                if force:
                    # Now we should re-link all deleted hardlinks, if exists
                    destDom.templateRelink(dstImgUUID, dstVolUUID)
            except se.StorageException:
                self.log.error("Unexpected error", exc_info=True)
                raise
            except Exception as e:
                self.log.error("Unexpected error", exc_info=True)
                raise se.CopyImageError("src image=%s, dst image=%s: msg=%s" %
                                        (srcImgUUID, dstImgUUID, str(e)))

            self.log.info("Finished copying %s:%s -> %s:%s", sdUUID,
                          srcVolUUID, dstSdUUID, dstVolUUID)
            # TODO: handle return status
            return dstVolUUID
        finally:
            self.__cleanupCopy(srcVol=srcVol, dstVol=dstVol)

    def calculate_initial_size(self, is_file, format, prealloc,
                               estimate):
        """
        Return the initial size for creating a volume during copyCollapsed.

        Arguments:
            is_file (bool): destination storage domain is file domain.
            format (int): destination volume format enum.
            prealloc (int): destination volume preallocation enum.
            estimate (int): estimated allocation in bytes.
        """
        if is_file:
            # Avoid slow preallocation of raw-preallocated volumes on file
            # based storage.
            if format == sc.RAW_FORMAT and prealloc == sc.PREALLOCATED_VOL:
                return 0
        else:
            # Ensure that enough extents are allocated for raw-preallocated
            # volume on block storage.
            # TODO: Calculate the value here.
            if format == sc.COW_FORMAT and prealloc == sc.SPARSE_VOL:
                return estimate

        # Otherwise no initial size is used.
        return None

    def calculate_vol_alloc(self, src_sd_id, src_vol_params,
                            dst_sd_id, dst_vol_format):
        """
        Calculate destination volume allocation size for copying source volume.

        Arguments:
            src_sd_id (str): Source volume storage domain id
            src_vol_params (dict): Dictionary returned from
                                   `storage.volume.Volume.getVolumeParams()`
            dst_sd_id (str): Destination volume storage domain id
            dst_vol_format (int): One of sc.RAW_FORMAT, sc.COW_FORMAT

        Returns:
            Volume allocation in bytes
        """
        if dst_vol_format == sc.RAW_FORMAT:
            # destination 'raw'.
            # The actual volume size must be the src virtual size.
            return src_vol_params['capacity']
        else:
            # destination 'cow'.
            # The actual volume size can be more than virtual size
            # due to qcow2 metadata.
            if src_vol_params['volFormat'] == sc.COW_FORMAT:
                # source 'cow'
                if src_vol_params['parent'] != sc.BLANK_UUID:
                    # source 'cow' with parent
                    # Using estimated size of the chain.
                    if src_vol_params['prealloc'] != sc.SPARSE_VOL:
                        raise se.IncorrectFormat(self)
                    return self.estimateChainSize(
                        src_sd_id,
                        src_vol_params['imgUUID'],
                        src_vol_params['volUUID'],
                        src_vol_params['capacity'])
                else:
                    # source 'cow' without parent.
                    # Use estimate for supporting compressed source images, for
                    # example, uploaded compressed qcow2 appliance.
                    return self.estimate_qcow2_size(src_vol_params, dst_sd_id)
            else:
                # source 'raw'.
                # Add additional space for qcow2 metadata.
                return self.estimate_qcow2_size(src_vol_params, dst_sd_id)

    def syncVolumeChain(self, sdUUID, imgUUID, volUUID, actualChain):
        """
        Fix volume metadata to reflect the given actual chain.  This function
        is used to correct the volume chain linkage after a live merge.
        """
        curChain = self.getChain(sdUUID, imgUUID, volUUID)
        log_str = logutils.volume_chain_to_str(vol.volUUID for vol in curChain)
        self.log.info("Current chain=%s ", log_str)

        subChain = []
        for vol in curChain:
            if vol.volUUID not in actualChain:
                subChain.insert(0, vol.volUUID)
            elif len(subChain) > 0:
                break
        if len(subChain) == 0:
            return
        self.log.info("Unlinking subchain: %s", subChain)

        sdDom = sdCache.produce(sdUUID=sdUUID)
        dstParent = sdDom.produceVolume(imgUUID, subChain[0]).getParent()
        subChainTailVol = sdDom.produceVolume(imgUUID, subChain[-1])
        if subChainTailVol.isLeaf():
            self.log.info("Leaf volume %s is being removed from the chain. "
                          "Marking it ILLEGAL to prevent data corruption",
                          subChainTailVol.volUUID)
            subChainTailVol.setLegality(sc.ILLEGAL_VOL)
        else:
            for childID in subChainTailVol.getChildren():
                self.log.info("Setting parent of volume %s to %s",
                              childID, dstParent)
                sdDom.produceVolume(imgUUID, childID). \
                    setParentMeta(dstParent)

    def reconcileVolumeChain(self, sdUUID, imgUUID, leafVolUUID):
        """
        Discover and return the actual volume chain of an offline image
        according to the qemu-img info command and synchronize volume metadata.
        """
        # Prepare volumes
        dom = sdCache.produce(sdUUID)
        allVols = dom.getAllVolumes()
        imgVolumes = sd.getVolsOfImage(allVols, imgUUID).keys()
        dom.activateVolumes(imgUUID, imgVolumes)

        # Walk the volume chain using qemu-img.  Not safe for running VMs
        actualVolumes = []
        volUUID = leafVolUUID
        while volUUID is not None:
            actualVolumes.insert(0, volUUID)
            vol = dom.produceVolume(imgUUID, volUUID)
            qemuImgFormat = sc.fmt2str(vol.getFormat())
            imgInfo = qemuimg.info(vol.volumePath, qemuImgFormat)
            backingFile = imgInfo.get('backingfile')
            if backingFile is not None:
                volUUID = os.path.basename(backingFile)
            else:
                volUUID = None

        # A merge of the active layer has copy and pivot phases.
        # During copy, data is copied from the leaf into its parent.  Writes
        # are mirrored to both volumes.  So even after copying is complete the
        # volumes will remain consistent.  Finally, the VM is pivoted from the
        # old leaf to the new leaf and mirroring to the old leaf ceases. During
        # mirroring and before pivoting, we mark the old leaf ILLEGAL so we
        # know it's safe to delete in case the operation is interrupted.
        vol = dom.produceVolume(imgUUID, leafVolUUID)
        if vol.getLegality() == sc.ILLEGAL_VOL:
            actualVolumes.remove(leafVolUUID)

        # Now that we know the correct volume chain, sync the storge metadata
        self.syncVolumeChain(sdUUID, imgUUID, actualVolumes[-1], actualVolumes)

        dom.deactivateImage(imgUUID)
        return actualVolumes

    def _activateVolumeForImportExport(self, domain, imgUUID, volUUID=None):
        chain = self.getChain(domain.sdUUID, imgUUID, volUUID)
        log_str = logutils.volume_chain_to_str(vol.volUUID for vol in chain)
        self.log.info("chain=%s ", log_str)

        template = chain[0].getParentVolume()
        if template or len(chain) > 1:
            self.log.error("Importing and exporting an image with more "
                           "than one volume is not supported")
            raise se.CopyImageError()

        domain.activateVolumes(imgUUID, volUUIDs=[chain[0].volUUID])
        return chain[0]

    def upload(self, methodArgs, sdUUID, imgUUID, volUUID=None):
        domain = sdCache.produce(sdUUID)

        vol = self._activateVolumeForImportExport(domain, imgUUID, volUUID)
        try:
            imageSharing.upload(vol.getVolumePath(), methodArgs)
        finally:
            domain.deactivateImage(imgUUID)

    def download(self, methodArgs, sdUUID, imgUUID, volUUID=None):
        domain = sdCache.produce(sdUUID)

        vol = self._activateVolumeForImportExport(domain, imgUUID, volUUID)
        try:
            # Extend the volume (if relevant) to the image size
            vol.extend(imageSharing.getSize(methodArgs))
            imageSharing.download(vol.getVolumePath(), methodArgs)
        finally:
            domain.deactivateImage(imgUUID)

    def copyFromImage(self, methodArgs, sdUUID, imgUUID, volUUID):
        domain = sdCache.produce(sdUUID)

        vol = self._activateVolumeForImportExport(domain, imgUUID, volUUID)
        try:
            imageSharing.copyFromImage(vol.getVolumePath(), methodArgs)
        finally:
            domain.deactivateImage(imgUUID)

    def copyToImage(self, methodArgs, sdUUID, imgUUID, volUUID=None):
        domain = sdCache.produce(sdUUID)

        vol = self._activateVolumeForImportExport(domain, imgUUID, volUUID)
        try:
            # Extend the volume (if relevant) to the image size
            vol.extend(imageSharing.getLengthFromArgs(methodArgs))
            imageSharing.copyToImage(vol.getVolumePath(), methodArgs)
        finally:
            domain.deactivateImage(imgUUID)
