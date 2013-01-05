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
from glob import glob

import sd
import fileSD
import fileUtils
import storage_exception as se
import misc


class LocalFsStorageDomain(fileSD.FileStorageDomain):

    @classmethod
    def _preCreateValidation(cls, sdUUID, domPath, typeSpecificArg, version):
        # Some trivial resource validation
        if os.path.abspath(typeSpecificArg) != typeSpecificArg:
            raise se.StorageDomainIllegalRemotePath(typeSpecificArg)

        fileSD.validateDirAccess(domPath)

        sd.validateDomainVersion(version)

        # Make sure there are no remnants of other domain
        mdpat = os.path.join(domPath, "*", sd.DOMAIN_META_DATA)
        if len(glob(mdpat)) > 0:
            raise se.StorageDomainNotEmpty(typeSpecificArg)

    @classmethod
    def create(cls, sdUUID, domainName, domClass, remotePath, storageType,
               version):
        """
        Create new storage domain.
            'sdUUID' - Storage Domain UUID
            'domainName' - storage domain name ("iso" or "data domain name")
            'domClass' - Data/Iso
            'remotePath' - /data2
            'storageType' - NFS_DOMAIN, LOCALFS_DOMAIN, &etc.
            'version' - DOMAIN_VERSIONS
        """
        cls.log.info("sdUUID=%s domainName=%s remotePath=%s "
                     "domClass=%s", sdUUID, domainName, remotePath, domClass)

        if not misc.isAscii(domainName) and not sd.supportsUnicode(version):
            raise se.UnicodeArgumentException()

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
        fileUtils.createdir(imagesDir)

        # create special imageUUID for ISO/Floppy volumes
        # Actually the local domain shouldn't be ISO, but
        # we can allow it for systems without NFS at all
        if domClass is sd.ISO_DOMAIN:
            isoDir = os.path.join(imagesDir, sd.ISO_IMAGE_UUID)
            fileUtils.createdir(isoDir)

        fsd = LocalFsStorageDomain(os.path.join(mntPoint, sdUUID))
        fsd.initSPMlease()

        return fsd

    @staticmethod
    def findDomainPath(sdUUID):
        for tmpSdUUID, domainPath in fileSD.scanDomains("_*"):
            if tmpSdUUID == sdUUID and isLocalFsDomain(domainPath):
                return domainPath
        else:
            raise se.StorageDomainDoesNotExist(sdUUID)

    def getRealPath(self):
        return os.readlink(self.mountpoint)


def isLocalFsDomain(domainPath):
    """This ancillary function differentiates local and mounted SD's.

    Assumed that a local SD can't be mounted.
    mounted: nfs, posixFS
    local: a link to a local dir in the /rhev/datacenter
    """
    mtab = open("/etc/mtab", "r").readlines()
    for mEntry in mtab:
        mPoint = mEntry.split()[1]
        if mPoint == '/':
            continue
        elif mPoint in domainPath:
            # This is a mounted domain therefore not exists as a local SD.
            isLocal = False
            break
    else:
        isLocal = True
    return isLocal


def findDomain(sdUUID):
    return LocalFsStorageDomain(LocalFsStorageDomain.findDomainPath(sdUUID))
