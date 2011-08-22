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
import fnmatch
import re

import sd
from sd import processPoolDict
import fileSD
import fileUtils
import storage_exception as se
from storage_connection import validateDirAccess

class NfsStorageDomain(fileSD.FileStorageDomain):

    @classmethod
    def _preCreateValidation(cls, sdUUID, domPath, typeSpecificArg, version):
        # Some trivial resource validation
        if ":" not in typeSpecificArg:
            raise se.StorageDomainIllegalRemotePath(typeSpecificArg)

        sd.validateDomainVersion(version)

        # Make sure the underlying file system is mounted
        if not fileUtils.isMounted(mountPoint=domPath, mountType=fileUtils.FSTYPE_NFS):
            raise se.StorageDomainFSNotMounted(typeSpecificArg)

        validateDirAccess(domPath)

        # Make sure there are no remnants of other domain
        mdpat = os.path.join(domPath, "*", sd.DOMAIN_META_DATA)
        if len(processPoolDict[sdUUID].glob.glob(mdpat)) > 0:
            raise se.StorageDomainNotEmpty(typeSpecificArg)

    @classmethod
    def create(cls, sdUUID, domainName, domClass, remotePath, storageType, version):
        """
        Create new storage domain.
            'sdUUID' - Storage Domain UUID
            'domainName' - storage domain name ("iso" or "data domain name")
            'remotePath' - server:/export_path
            'domClass' - Data/Iso
        """
        cls.log.info("sdUUID=%s domainName=%s remotePath=%s "
            "domClass=%s", sdUUID, domainName, remotePath, domClass)

        # Create local path
        mntPath = fileUtils.transformPath(remotePath)

        mntPoint = os.path.join(cls.storage_repository,
            sd.DOMAIN_MNT_POINT, mntPath)

        cls._preCreateValidation(sdUUID, mntPoint, remotePath, version)

        domainDir = os.path.join(mntPoint, sdUUID)
        cls._prepareMetadata(domainDir, sdUUID, domainName, domClass,
                            remotePath, storageType, version)

        # create domain images folder
        imagesDir = os.path.join(domainDir, sd.DOMAIN_IMAGES)
        processPoolDict[sdUUID].fileUtils.createdir(imagesDir)

        # create special imageUUID for ISO/Floppy volumes
        if domClass is sd.ISO_DOMAIN:
            isoDir = os.path.join(imagesDir, sd.ISO_IMAGE_UUID)
            processPoolDict[sdUUID].fileUtils.createdir(isoDir)

        fsd = NfsStorageDomain(os.path.join(mntPoint, sdUUID))
        fsd.initSPMlease()

        return fsd

    def getFileList(self, pattern, caseSensitive):
        """
        Returns a list of all files in the domain filtered according to extension.
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
        filePrefixLen = len(basedir)+1
        for entry in filesList:
            st = self.oop.os.stat(entry)
            stats = {'size':str(st.st_size), 'ctime':str(st.st_ctime)}

            try:
                self.oop.fileUtils.validateQemuReadable(entry)
                stats['status'] = 0  # Status OK
            except se.StorageServerAccessPermissionError:
                stats['status'] = se.StorageServerAccessPermissionError.code

            fileName = entry[filePrefixLen:]
            filesDict[fileName] = stats
        return filesDict

    def selftest(self):
        """
        Run internal self test
        """
        if not fileUtils.isMounted(mountPoint=self.mountpoint, mountType=fileUtils.FSTYPE_NFS):
            raise se.StorageDomainFSNotMounted

        # Run general part of selftest
        return fileSD.FileStorageDomain.selftest(self)


    @staticmethod
    def findDomainPath(sdUUID):
        for tmpSdUUID, domainPath in fileSD.scanDomains("[!_]*:*"):
            if tmpSdUUID == sdUUID:
                return domainPath

        raise se.StorageDomainDoesNotExist(sdUUID)

def findDomain(sdUUID):
    return NfsStorageDomain(NfsStorageDomain.findDomainPath(sdUUID))

