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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301  USA
#
# Refer to the README and COPYING files for full details of the license
#

import os
import errno
import logging
import glob

import sd
import storage_exception as se
import fileVolume
import image
import misc
import outOfProcess as oop
from vdsm.config import config
from processPool import Timeout
from persistentDict import PersistentDict, DictValidator
from vdsm import constants
import time
import supervdsm
import mount

REMOTE_PATH = "REMOTE_PATH"

FILE_SD_MD_FIELDS = sd.SD_MD_FIELDS.copy()
# TBD: Do we really need this key?
FILE_SD_MD_FIELDS[REMOTE_PATH] = (str, str)

getProcPool = oop.getGlobalProcPool

def validateDirAccess(dirPath):
    getProcPool().fileUtils.validateAccess(dirPath)
    supervdsm.getProxy().validateAccess(constants.QEMU_PROCESS_USER,
            (constants.DISKIMAGE_GROUP, constants.METADATA_GROUP), dirPath,
            (os.R_OK | os.X_OK))


def getDomUuidFromMetafilePath(metafile):
    # Metafile path has pattern:
    #  /rhev/data-center/mnt/export-path/sdUUID/dom_md/metadata

    # sdUUID position after data-center
    sdUUIDPos = 3

    metaList = metafile.split('/')
    sdUUID = len(os.path.normpath(config.get('irs', 'repository')).split('/')) + sdUUIDPos
    return metaList[sdUUID]

class FileMetadataRW(object):
    """
    FileSDMetadata implements metadata extractor/committer over a simple file
    """

    def __init__(self, metafile):
        # FileSDMetadata is kept in the file
        self._metafile = metafile
        self._sdUUID = getDomUuidFromMetafilePath(metafile)
        self._oop = oop.getProcessPool(self._sdUUID)

    def readlines(self):
        if not self._oop.fileUtils.pathExists(self._metafile):
                return []
        return misc.stripNewLines(self._oop.directReadLines(self._metafile))

    def writelines(self, metadata):
        for i, line in enumerate(metadata):
            if isinstance(line, unicode):
                line = line.encode('utf-8')
            metadata[i] = line

        metadata = [i + '\n' for i in metadata]
        tmpFilePath = self._metafile + ".new"
        try:
            self._oop.writeLines(tmpFilePath, metadata)
        except IOError, e:
            if e.errno != errno.ESTALE:
                raise
            self._oop.writeLines(tmpFilePath, metadata)
        self._oop.os.rename(tmpFilePath, self._metafile)

FileSDMetadata = lambda metafile: DictValidator(PersistentDict(FileMetadataRW(metafile)), FILE_SD_MD_FIELDS)

class FileStorageDomain(sd.StorageDomain):
    def __init__(self, domainPath):
        # Using glob might look like the simplest thing to do but it isn't
        # If one of the mounts is stuck it'll cause the entire glob to fail
        # and you wouldn't be able to access any domain
        self.log.debug("Reading domain in path %s", domainPath)
        self.mountpoint = os.path.dirname(domainPath)
        self.remotePath = os.path.basename(self.mountpoint)
        self.metafile = os.path.join(domainPath, sd.DOMAIN_META_DATA, sd.METADATA)

        metadata = FileSDMetadata(self.metafile)
        sdUUID = metadata[sd.DMDK_SDUUID]
        domaindir = os.path.join(self.mountpoint, sdUUID)
        sd.StorageDomain.__init__(self, sdUUID, domaindir, metadata)

        if not self.oop.fileUtils.pathExists(self.metafile):
            raise se.StorageDomainMetadataNotFound(sdUUID, self.metafile)
        self.imageGarbageCollector()
        self._registerResourceNamespaces()

    @classmethod
    def _prepareMetadata(cls, domPath, sdUUID, domainName, domClass, remotePath, storageType, version):
        """
        Prepare all domain's special volumes and metadata
        """
        # create domain metadata folder
        metadataDir = os.path.join(domPath, sd.DOMAIN_META_DATA)

        procPool = oop.getProcessPool(sdUUID)
        procPool.fileUtils.createdir(metadataDir, 0775)

        for metaFile in (sd.LEASES, sd.IDS, sd.INBOX, sd.OUTBOX):
            try:
                procPool.createSparseFile(
                                os.path.join(metadataDir, metaFile), 0, 0660)
            except Exception, e:
                raise se.StorageDomainMetadataCreationError(
                    "create meta file '%s' failed: %s" % (metaFile, str(e)))

        metaFile = os.path.join(metadataDir, sd.METADATA)

        md = FileSDMetadata(metaFile)
        # initialize domain metadata content
        # FIXME : This is 99% like the metadata in block SD
        #         Do we really need to keep the EXPORT_PATH?
        #         no one uses it
        md.update({
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
                REMOTE_PATH : remotePath
                })

    def getReadDelay(self):
        t = time.time()
        oop.getProcessPool(self.sdUUID).directReadLines(self.metafile)
        return time.time() - t

    def produceVolume(self, imgUUID, volUUID):
        """
        Produce a type specific volume object
        """
        repoPath = self._getRepoPath()
        return fileVolume.FileVolume(repoPath, self.sdUUID, imgUUID, volUUID)


    def getVolumeClass(self):
        """
        Return a type specific volume generator object
        """
        return fileVolume.FileVolume


    def volumeExists(self, imgPath, volUUID):
        """
        Return True if the volume volUUID exists
        """
        volPath = os.path.join(imgPath, volUUID)
        return self.oop.fileUtils.pathExists(volPath)


    @classmethod
    def validateCreateVolumeParams(cls, volFormat, preallocate, srcVolUUID):
        """
        Validate create volume parameters.
            'srcVolUUID' - backing volume UUID
            'volFormat' - volume format RAW/QCOW2
            'preallocate' - sparse/preallocate
        """
        fileVolume.FileVolume.validateCreateVolumeParams(volFormat, preallocate, srcVolUUID)


    def createVolume(self, imgUUID, size, volFormat, preallocate, diskType, volUUID, desc, srcImgUUID, srcVolUUID):
        """
        Create a new volume
        """
        repoPath = self._getRepoPath()
        return fileVolume.FileVolume.create(repoPath, self.sdUUID,
                            imgUUID, size, volFormat, preallocate, diskType,
                            volUUID, desc, srcImgUUID, srcVolUUID)

    def getVolumeLease(self, imgUUID, volUUID):
        """
        Return the volume lease (leasePath, leaseOffset)
        """
        if self.hasVolumeLeases():
            vol = self.produceVolume(imgUUID, volUUID)
            volumePath = vol.getVolumePath()
            leasePath = volumePath + fileVolume.LEASE_FILEEXT
            return leasePath, fileVolume.LEASE_FILEOFFSET
        return None, None

    def validate(self, useCache=False):
        """
        Validate that the storage domain is accessible.
        """
        self.log.info("sdUUID=%s", self.sdUUID)
        if not useCache:
            self.invalidateMetadata()
        self.getMetadata()

    def validateMasterMount(self):
         return self.oop.fileUtils.pathExists(self.getMasterDir())

    def getAllImages(self):
        """
        Fetch the list of the Image UUIDs
        """
        # Get Volumes of an image
        pattern = os.path.join(self.storage_repository,
                               # ISO domains don't have images,
                               # we can assume single domain
                               self.getPools()[0],
                               self.sdUUID, sd.DOMAIN_IMAGES)
        pattern = os.path.join(pattern, constants.UUID_GLOB_PATTERN)
        files = self.oop.glob.glob(pattern)
        imgList = []
        for i in files:
            if self.oop.os.path.isdir(i):
                imgList.append(os.path.basename(i))
        return imgList

    def getAllVolumes(self):
        """
        Return dict {volUUID: ((imgUUIDs,), parentUUID)} of the domain.

        Template self image is the 1st term in teplate volume entry images.
        The parent can't be determined in file domain without reading the
        metadata.
        Setting parent = None for compatibility with block version.
        """
        volMetaPattern = os.path.join(self.mountpoint, self.sdUUID, sd.DOMAIN_IMAGES, "*", "*.meta")
        volMetaPaths = self.oop.glob.glob(volMetaPattern)
        volumes = {}
        for metaPath in volMetaPaths:
            head, tail = os.path.split(metaPath)
            volUUID, volExt = os.path.splitext(tail)
            imgUUID = os.path.basename(head)
            if volumes.has_key(volUUID):
                # Templates have no parents
                volumes[volUUID]['parent'] = sd.BLANK_UUID
                # Template volumes are hard linked in every image directory
                # which is derived from that template, therefore:
                # 1. a template volume which is in use will appear at least
                # twice (in the template image dir and in the derived image dir)
                # 2. Any volume which appears more than once in the dir tree is
                # by definition a template volume.
                # 3. Any image which has more than 1 volume is not a template
                # image. Therefore if imgUUID appears in more than one path then
                # it is not a template.
                if len(tuple(vPath for vPath in volMetaPaths
                        if imgUUID in vPath)) > 1:
                    # Add template additonal image
                    volumes[volUUID]['imgs'].append(imgUUID)
                else:
                    #Insert at head the template self image
                    volumes[volUUID]['imgs'].insert(0, imgUUID)
            else:
                volumes[volUUID] = {'imgs': [imgUUID], 'parent': None}
        return dict((k, sd.ImgsPar(tuple(v['imgs']), v['parent'])) for k, v in volumes.iteritems())

    @classmethod
    def format(cls, sdUUID):
        """
        Format detached storage domain.
        This removes all data from the storage domain.
        """
        cls.log.info("Formating domain %s", sdUUID)
        try:
            domaindir = cls.findDomainPath(sdUUID)
        except (se.StorageDomainDoesNotExist):
            pass
        else:
            oop.getProcessPool(sdUUID).fileUtils.cleanupdir(domaindir, ignoreErrors = False)
        return True

    def getRemotePath(self):
        return self.remotePath

    def getRealPath(self):
        """
        Return the actual path to the underlying storage.
        This function needs to be overloaded by the child classes.
        """
        return ""

    def getInfo(self):
        """
        Get storage domain info
        """
        ##self.log.info("sdUUID=%s", self.sdUUID)
        # First call parent getInfo() - it fills in all the common details
        info = sd.StorageDomain.getInfo(self)
        # Now add fileSD specific data
        info['remotePath'] = self.getRealPath()
        return info

    def getStats(self):
        """
        Get storage domain statistics
        """
        ##self.log.info("sdUUID=%s", self.sdUUID)
        stats = {'disktotal':'', 'diskfree':'', 'mdavalid':True, 'mdathreshold':True,
                 'mdasize':0, 'mdafree':0}
        try:
            st = self.oop.os.statvfs(self.domaindir)
            stats['disktotal'] = str(st.f_frsize * st.f_blocks)
            stats['diskfree'] = str(st.f_frsize * st.f_bavail)
        except OSError, e:
            self.log.info("sdUUID=%s %s", self.sdUUID, str(e))
            if e.errno == errno.ESTALE:
                raise se.FileStorageDomainStaleNFSHandle
            raise se.StorageDomainAccessError(self.sdUUID)
        return stats

    def mountMaster(self):
        """
        Mount the master metadata file system. Should be called only by SPM.
        """
        masterdir = os.path.join(self.domaindir, sd.MASTER_FS_DIR)
        if not self.oop.fileUtils.pathExists(masterdir):
            self.oop.os.mkdir(masterdir, 0755)

    def unmountMaster(self):
        """
        Unmount the master metadata file system. Should be called only by SPM.
        """
        pass


    def selftest(self):
        """
        Run internal self test
        """
        try:
            self.oop.os.statvfs(self.domaindir)
        except OSError, e:
            if e.errno == errno.ESTALE:
                # In case it is "Stale NFS handle" we are taking preventive
                # measures and unmounting this NFS resource. Chances are
                # that is the most intelligent thing we can do in this
                # situation anyway.
                self.log.debug("Unmounting stale file system %s", self.mountpoint)
                mount.getMountFromTarget(self.mountpoint).umount()
                raise se.FileStorageDomainStaleNFSHandle()
            raise

    def imageGarbageCollector(self):
        """
        Image Garbage Collector
        remove the remnants of the removed images (they could be left sometimes
        (on NFS mostly) due to lazy file removal
        """
        removedPattern = os.path.join(self.domaindir, sd.DOMAIN_IMAGES,
            image.REMOVED_IMAGE_PREFIX+'*')
        removedImages = self.oop.glob.glob(removedPattern)
        self.log.debug("Removing remnants of deleted images %s" % removedImages)
        for imageDir in removedImages:
            self.oop.fileUtils.cleanupdir(imageDir)

def getMountsList(pattern="*"):
    finalPat = os.path.join(sd.StorageDomain.storage_repository,
                            sd.DOMAIN_MNT_POINT, pattern)
    mntList = glob.glob(finalPat)
    # For pattern='*' in mixed pool (block and file domains)
    # glob will return sd.BLOCKSD_DIR among of real mount points.
    # Remove sd.BLOCKSD_DIR from glob results.
    return [ mnt for mnt in mntList if not mnt.endswith('/'+sd.BLOCKSD_DIR) ]

def scanDomains(pattern="*"):
    log = logging.getLogger("scanDomains")

    mntList = getMountsList(pattern)

    def collectMetaFiles(possibleDomain):
        try:
            metaFiles = oop.getGlobalProcPool().glob.glob(os.path.join(possibleDomain,
                constants.UUID_GLOB_PATTERN, sd.DOMAIN_META_DATA))

            for metaFile in metaFiles:
                if os.path.basename(os.path.dirname(metaFile)) != sd.MASTER_FS_DIR:
                    sdUUID = os.path.basename(os.path.dirname(metaFile))

                    return (sdUUID, os.path.dirname(metaFile))

        except Timeout:
            pass
        except Exception:
            log.warn("Could not collect metadata file for domain path %s", possibleDomain, exc_info=True)

    for res in misc.itmap(collectMetaFiles, mntList):
        if res is None:
            continue

        yield res

def getStorageDomainsList():
    return [item[0] for item in scanDomains()]
