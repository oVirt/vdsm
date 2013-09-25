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
import errno
import logging
import glob
import fnmatch
import re

import sd
import storage_exception as se
import fileUtils
import fileVolume
import misc
import outOfProcess as oop
from remoteFileHandler import Timeout
from persistentDict import PersistentDict, DictValidator
from vdsm import constants
from vdsm.utils import stripNewLines
import supervdsm
import mount

REMOTE_PATH = "REMOTE_PATH"

FILE_SD_MD_FIELDS = sd.SD_MD_FIELDS.copy()
# TBD: Do we really need this key?
FILE_SD_MD_FIELDS[REMOTE_PATH] = (str, str)

getProcPool = oop.getGlobalProcPool


def validateDirAccess(dirPath):
    try:
        getProcPool().fileUtils.validateAccess(dirPath)
        supervdsm.getProxy().validateAccess(
            constants.QEMU_PROCESS_USER,
            (constants.DISKIMAGE_GROUP, constants.METADATA_GROUP), dirPath,
            (os.R_OK | os.X_OK))
    except OSError as e:
        if e.errno == errno.EACCES:
            raise se.StorageServerAccessPermissionError(dirPath)
        raise

    return True


def validateFileSystemFeatures(sdUUID, mountDir):
    try:
        # Don't unlink this file, we don't have the cluster lock yet as it
        # requires direct IO which is what we are trying to test for. This
        # means that unlinking the file might cause a race. Since we don't
        # care what the content of the file is, just that we managed to
        # open it O_DIRECT.
        testFilePath = os.path.join(mountDir, "__DIRECT_IO_TEST__")
        oop.getProcessPool(sdUUID).directTouch(testFilePath)
    except OSError as e:
        if e.errno == errno.EINVAL:
            log = logging.getLogger("Storage.fileSD")
            log.error("Underlying file system doesn't support"
                      "direct IO")
            raise se.StorageDomainTargetUnsupported()

        raise


def getDomPath(sdUUID):
    pattern = os.path.join(sd.StorageDomain.storage_repository,
                           sd.DOMAIN_MNT_POINT, '*', sdUUID)
    # Warning! You need a global proc pool big as the number of NFS domains.
    domPaths = getProcPool().glob.glob(pattern)
    if len(domPaths) == 0:
        raise se.StorageDomainDoesNotExist(sdUUID)
    elif len(domPaths) > 1:
        raise se.StorageDomainLayoutError(sdUUID)
    else:
        return domPaths[0]


def getImagePath(sdUUID, imgUUID):
    return os.path.join(getDomPath(sdUUID), 'images', imgUUID)


def getDomUuidFromMetafilePath(metafile):
    # Metafile path has pattern:
    #  /rhev/data-center/mnt/export-path/sdUUID/dom_md/metadata

    metaList = os.path.normpath(metafile).split('/')
    return metaList[-3]


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
        try:
            return stripNewLines(self._oop.directReadLines(self._metafile))
        except (IOError, OSError) as e:
            if e.errno != errno.ENOENT:
                raise
            return []

    def writelines(self, metadata):
        for i, line in enumerate(metadata):
            if isinstance(line, unicode):
                line = line.encode('utf-8')
            metadata[i] = line

        metadata = [i + '\n' for i in metadata]
        tmpFilePath = self._metafile + ".new"
        try:
            self._oop.writeLines(tmpFilePath, metadata)
        except IOError as e:
            if e.errno != errno.ESTALE:
                raise
            self._oop.writeLines(tmpFilePath, metadata)
        self._oop.os.rename(tmpFilePath, self._metafile)


FileSDMetadata = lambda metafile: DictValidator(
    PersistentDict(FileMetadataRW(metafile)), FILE_SD_MD_FIELDS)


class FileStorageDomain(sd.StorageDomain):
    def __init__(self, domainPath):
        # Using glob might look like the simplest thing to do but it isn't
        # If one of the mounts is stuck it'll cause the entire glob to fail
        # and you wouldn't be able to access any domain
        self.log.debug("Reading domain in path %s", domainPath)
        self.mountpoint = os.path.dirname(domainPath)
        self.remotePath = os.path.basename(self.mountpoint)
        self.metafile = os.path.join(domainPath, sd.DOMAIN_META_DATA,
                                     sd.METADATA)

        sdUUID = os.path.basename(domainPath)
        validateFileSystemFeatures(sdUUID, self.mountpoint)

        metadata = FileSDMetadata(self.metafile)
        domaindir = os.path.join(self.mountpoint, sdUUID)
        sd.StorageDomain.__init__(self, sdUUID, domaindir, metadata)

        if not self.oop.fileUtils.pathExists(self.metafile):
            raise se.StorageDomainMetadataNotFound(sdUUID, self.metafile)
        self.imageGarbageCollector()
        self._registerResourceNamespaces()

    @property
    def supportsSparseness(self):
        """
        This property advertises whether the storage domain supports
        sparseness or not.
        """
        return True

    def setMetadataPermissions(self):
        procPool = oop.getProcessPool(self.sdUUID)
        for metaFile in (sd.LEASES, sd.IDS, sd.INBOX, sd.OUTBOX):
            try:
                fpath = os.path.join(self.getMDPath(), metaFile)
                procPool.os.chmod(fpath, 0660)
            except Exception as e:
                raise se.StorageDomainMetadataCreationError(
                    "Lease permission change file '%s' failed: %s"
                    % (metaFile, e))

    @classmethod
    def _prepareMetadata(cls, domPath, sdUUID, domainName, domClass,
                         remotePath, storageType, version):
        """
        Prepare all domain's special volumes and metadata
        """
        # create domain metadata folder
        metadataDir = os.path.join(domPath, sd.DOMAIN_META_DATA)

        procPool = oop.getProcessPool(sdUUID)
        procPool.fileUtils.createdir(metadataDir, 0775)

        for metaFile in (sd.LEASES, sd.IDS, sd.INBOX, sd.OUTBOX):
            try:
                procPool.truncateFile(
                    os.path.join(metadataDir, metaFile), 0, 0660)
            except Exception as e:
                raise se.StorageDomainMetadataCreationError(
                    "create meta file '%s' failed: %s" % (metaFile, str(e)))

        metaFile = os.path.join(metadataDir, sd.METADATA)

        md = FileSDMetadata(metaFile)
        # initialize domain metadata content
        # FIXME : This is 99% like the metadata in block SD
        #         Do we really need to keep the EXPORT_PATH?
        #         no one uses it
        md.update({
            sd.DMDK_VERSION: version,
            sd.DMDK_SDUUID: sdUUID,
            sd.DMDK_TYPE: storageType,
            sd.DMDK_CLASS: domClass,
            sd.DMDK_DESCRIPTION: domainName,
            sd.DMDK_ROLE: sd.REGULAR_DOMAIN,
            sd.DMDK_POOLS: [],
            sd.DMDK_LOCK_POLICY: '',
            sd.DMDK_LOCK_RENEWAL_INTERVAL_SEC:
            sd.DEFAULT_LEASE_PARAMS[sd.DMDK_LOCK_RENEWAL_INTERVAL_SEC],
            sd.DMDK_LEASE_TIME_SEC: sd.DEFAULT_LEASE_PARAMS[
                sd.DMDK_LEASE_TIME_SEC],
            sd.DMDK_IO_OP_TIMEOUT_SEC:
            sd.DEFAULT_LEASE_PARAMS[sd.DMDK_IO_OP_TIMEOUT_SEC],
            sd.DMDK_LEASE_RETRIES:
            sd.DEFAULT_LEASE_PARAMS[sd.DMDK_LEASE_RETRIES],
            REMOTE_PATH: remotePath
        })

    def getReadDelay(self):
        stats = misc.readspeed(self.metafile, 4096)
        return stats['seconds']

    def getFileList(self, pattern, caseSensitive):
        """
        Returns a list of all files in the domain filtered according to
        extension.
        """
        basedir = self.getIsoDomainImagesDir()
        filesList = self.oop.simpleWalk(basedir)

        if pattern != '*':
            if caseSensitive:
                filesList = fnmatch.filter(filesList, pattern)
            else:
                regex = fnmatch.translate(pattern)
                reobj = re.compile(regex, re.IGNORECASE)
                filesList = [f for f in filesList if reobj.match(f)]

        filesDict = {}
        filePrefixLen = len(basedir) + 1
        for entry in filesList:
            st = self.oop.os.stat(entry)
            stats = {'size': str(st.st_size), 'ctime': str(st.st_ctime)}

            try:
                self.oop.fileUtils.validateQemuReadable(entry)
                stats['status'] = 0  # Status OK
            except OSError as e:
                if e.errno != errno.EACCES:
                    raise

                stats['status'] = se.StorageServerAccessPermissionError.code

            fileName = entry[filePrefixLen:]
            filesDict[fileName] = stats
        return filesDict

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
        fileVolume.FileVolume.validateCreateVolumeParams(
            volFormat, preallocate, srcVolUUID)

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

    def validate(self):
        """
        Validate that the storage domain is accessible.
        """
        self.log.info("sdUUID=%s", self.sdUUID)
        self.invalidateMetadata()
        if not len(self.getMetadata()):
            raise se.StorageDomainAccessError(self.sdUUID)

    def validateMasterMount(self):
        return self.oop.fileUtils.pathExists(self.getMasterDir())

    def getAllImages(self):
        """
        Fetch the set of the Image UUIDs in the SD.
        """
        # Get Volumes of an image
        pattern = os.path.join(self.storage_repository,
                               # ISO domains don't have images,
                               # we can assume single domain
                               self.getPools()[0],
                               self.sdUUID, sd.DOMAIN_IMAGES)
        pattern = os.path.join(pattern, constants.UUID_GLOB_PATTERN)
        files = self.oop.glob.glob(pattern)
        images = set()
        for i in files:
            if self.oop.os.path.isdir(i):
                images.add(os.path.basename(i))
        return images

    def deleteImage(self, sdUUID, imgUUID, volsImgs):
        currImgDir = getImagePath(sdUUID, imgUUID)
        dirName, baseName = os.path.split(currImgDir)
        toDelDir = os.tempnam(dirName, sd.REMOVED_IMAGE_PREFIX + baseName)
        try:
            self.oop.os.rename(currImgDir, toDelDir)
        except OSError as e:
            self.log.error("image: %s can't be moved", currImgDir)
            raise se.ImageDeleteError("%s %s" % (imgUUID, str(e)))
        for volUUID in volsImgs:
            volPath = os.path.join(toDelDir, volUUID)
            try:
                self.oop.os.remove(volPath)
                self.oop.os.remove(volPath + '.meta')
                self.oop.os.remove(volPath + '.lease')
            except OSError:
                self.log.error("vol: %s can't be removed.",
                               volPath, exc_info=True)
        try:
            self.oop.os.rmdir(toDelDir)
        except OSError as e:
            self.log.error("removed image dir: %s can't be removed", toDelDir)
            raise se.ImageDeleteError("%s %s" % (imgUUID, str(e)))

    def zeroImage(self, sdUUID, imgUUID, volsImgs):
        self.log.warning("image %s on a fileSD %s won't be zeroed." %
                         (imgUUID, sdUUID))
        self.deleteImage(sdUUID, imgUUID, volsImgs)

    def deactivateImage(self, imgUUID):
        """
        Deactivate all the volumes belonging to the image.

        imgUUID: the image to be deactivated.
        """
        pass

    def getAllVolumes(self):
        """
        Return dict {volUUID: ((imgUUIDs,), parentUUID)} of the domain.

        Template self image is the 1st term in template volume entry images.
        The parent can't be determined in file domain without reading the
        metadata.
        Setting parent = None for compatibility with block version.
        """
        volMetaPattern = os.path.join(self.mountpoint, self.sdUUID,
                                      sd.DOMAIN_IMAGES, "*", "*.meta")
        volMetaPaths = self.oop.glob.glob(volMetaPattern)
        volumes = {}
        for metaPath in volMetaPaths:
            head, tail = os.path.split(metaPath)
            volUUID, volExt = os.path.splitext(tail)
            imgUUID = os.path.basename(head)
            if volUUID in volumes:
                # Templates have no parents
                volumes[volUUID]['parent'] = sd.BLANK_UUID
                # Template volumes are hard linked in every image directory
                # which is derived from that template, therefore:
                # 1. a template volume which is in use will appear at least
                # twice (in the template image dir and in the derived image
                # dir)
                # 2. Any volume which appears more than once in the dir tree is
                # by definition a template volume.
                # 3. Any image which has more than 1 volume is not a template
                # image. Therefore if imgUUID appears in more than one path
                # then it is not a template.
                if len(tuple(vPath for vPath in volMetaPaths
                             if imgUUID in vPath)) > 1:
                    # Add template additonal image
                    volumes[volUUID]['imgs'].append(imgUUID)
                else:
                    # Insert at head the template self image
                    volumes[volUUID]['imgs'].insert(0, imgUUID)
            else:
                volumes[volUUID] = {'imgs': [imgUUID], 'parent': None}
        return dict((k, sd.ImgsPar(tuple(v['imgs']), v['parent']))
                    for k, v in volumes.iteritems())

    def createImageLinks(self, srcImgPath, imgUUID):
        """
        qcow chain is build by reading each qcow header and reading the path
        to the parent. When creating the qcow layer, we pass a relative path
        which allows us to build a directory with links to all volumes in the
        chain anywhere we want. This method creates a directory with the image
        uuid under /var/run/vdsm and creates sym links to all the volumes in
        the chain.

        srcImgPath: Dir where the image volumes are.
        """
        sdRunDir = os.path.join(constants.P_VDSM_STORAGE, self.sdUUID)
        fileUtils.createdir(sdRunDir)
        imgRunDir = os.path.join(sdRunDir, imgUUID)
        try:
            os.symlink(srcImgPath, imgRunDir)
        except OSError as e:
            if e.errno == errno.EEXIST:
                self.log.debug("img run dir already exists: %s", imgRunDir)
            else:
                self.log.error("Failed to create img run dir: %s", imgRunDir)
                raise

        return imgRunDir

    def activateVolumes(self, imgUUID, volUUIDs):
        """
        Activate all the volumes listed in volUUIDs
        """
        # Volumes leaves created in 2.2 did not have group writeable bit
        # set. We have to set it here if we want qemu-kvm to write to old
        # NFS volumes. In theory it is necessary to fix the permission
        # of the leaf only but to not introduce an additional requirement
        # (ordered volUUIDs) we fix them all.
        imgDir = os.path.join(self.mountpoint, self.sdUUID, sd.DOMAIN_IMAGES,
                              imgUUID)
        volPaths = tuple(os.path.join(imgDir, v) for v in volUUIDs)
        for volPath in volPaths:
            self.oop.fileUtils.copyUserModeToGroup(volPath)

        return self.createImageLinks(imgDir, imgUUID)

    @classmethod
    def format(cls, sdUUID):
        """
        Format detached storage domain.
        This removes all data from the storage domain.
        """
        cls.log.info("Formatting domain %s", sdUUID)
        try:
            domaindir = cls.findDomainPath(sdUUID)
        except (se.StorageDomainDoesNotExist):
            pass
        else:
            try:
                oop.getProcessPool(sdUUID).fileUtils.cleanupdir(
                    domaindir, ignoreErrors=False)
            except RuntimeError as e:
                raise se.MiscDirCleanupFailure(str(e))

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
        # self.log.info("sdUUID=%s", self.sdUUID)
        # First call parent getInfo() - it fills in all the common details
        info = sd.StorageDomain.getInfo(self)
        # Now add fileSD specific data
        info['remotePath'] = self.getRealPath()
        return info

    def getStats(self):
        """
        Get storage domain statistics
        """
        # self.log.info("sdUUID=%s", self.sdUUID)
        stats = {'disktotal': '',
                 'diskfree': '',
                 'mdavalid': True,
                 'mdathreshold': True,
                 'mdasize': 0,
                 'mdafree': 0}
        try:
            st = self.oop.os.statvfs(self.domaindir)
            stats['disktotal'] = str(st.f_frsize * st.f_blocks)
            stats['diskfree'] = str(st.f_frsize * st.f_bavail)
        except OSError as e:
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
        except OSError as e:
            if e.errno == errno.ESTALE:
                # In case it is "Stale NFS handle" we are taking preventive
                # measures and unmounting this NFS resource. Chances are
                # that is the most intelligent thing we can do in this
                # situation anyway.
                self.log.debug("Unmounting stale file system %s",
                               self.mountpoint)
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
                                      sd.REMOVED_IMAGE_PREFIX + '*')
        removedImages = self.oop.glob.glob(removedPattern)
        self.log.debug("Removing remnants of deleted images %s" %
                       removedImages)
        for imageDir in removedImages:
            self.oop.fileUtils.cleanupdir(imageDir)

    def templateRelink(self, imgUUID, volUUID):
        """
        Relink all hardlinks of the template 'volUUID' in all VMs based on it.

        This function assumes that template image is used by other volumes.
        """
        allVols = self.getAllVolumes()
        tImgs = allVols[volUUID].imgs
        if len(tImgs) < 2:
            self.log.debug("Volume %s is an unused template or a regular "
                           "volume. Found  in images: %s allVols: %s", volUUID,
                           tImgs, allVols)
            return
        templateImage = tImgs[0]
        relinkImgs = tuple(tImgs[1:])
        repoPath = self._getRepoPath()
        basePath = os.path.join(repoPath, self.sdUUID, sd.DOMAIN_IMAGES)
        volFiles = [volUUID, volUUID + fileVolume.META_FILEEXT]
        if self.hasVolumeLeases():
            volFiles.append(volUUID + fileVolume.LEASE_FILEEXT)
        for rImg in relinkImgs:
            # This function assumes that all relevant images and template
            # namespaces are locked.
            for volFile in volFiles:
                tLink = os.path.join(basePath, rImg, volFile)
                tVol = os.path.join(basePath, templateImage, volFile)
                self.oop.utils.forceLink(tVol, tLink)


def getMountsList(pattern="*"):
    finalPat = os.path.join(sd.StorageDomain.storage_repository,
                            sd.DOMAIN_MNT_POINT, pattern)
    mntList = glob.glob(finalPat)
    # For pattern='*' in mixed pool (block and file domains)
    # glob will return sd.BLOCKSD_DIR among of real mount points.
    # Remove sd.BLOCKSD_DIR from glob results.
    return [mnt for mnt in mntList if not mnt.endswith('/' + sd.BLOCKSD_DIR)]


def scanDomains(pattern="*"):
    log = logging.getLogger("scanDomains")

    mntList = getMountsList(pattern)

    def collectMetaFiles(possibleDomain):
        try:
            metaFiles = oop.getProcessPool(possibleDomain).glob.glob(
                os.path.join(possibleDomain,
                             constants.UUID_GLOB_PATTERN,
                             sd.DOMAIN_META_DATA))

            for metaFile in metaFiles:
                if (os.path.basename(os.path.dirname(metaFile)) !=
                        sd.MASTER_FS_DIR):
                    sdUUID = os.path.basename(os.path.dirname(metaFile))

                    return (sdUUID, os.path.dirname(metaFile))

        except Timeout:
            log.warn("Metadata collection for domain path %s timedout",
                     possibleDomain, exc_info=True)
        except Exception:
            log.warn("Could not collect metadata file for domain path %s",
                     possibleDomain, exc_info=True)

    # Run collectMetaFiles in extenral processes.
    # The amount of processes that can be initiated in the same time is the
    # amount of stuck domains we are willing to handle +1.
    # We Use 30% of the available slots.
    # TODO: calculate it right, now we use same value of max process per
    #       domain.
    for res in misc.itmap(collectMetaFiles, mntList, oop.HELPERS_PER_DOMAIN):
        if res is None:
            continue

        yield res


def getStorageDomainsList():
    return [item[0] for item in scanDomains()]
