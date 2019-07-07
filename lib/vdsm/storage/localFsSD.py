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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#

from __future__ import absolute_import

import os
from glob import glob

from vdsm.storage import clusterlock
from vdsm.storage import constants as sc
from vdsm.storage import exception as se
from vdsm.storage import fileSD
from vdsm.storage import fileUtils
from vdsm.storage import misc
from vdsm.storage import sd


class LocalFsStorageDomainManifest(fileSD.FileStorageDomainManifest):
    # version: clusterLockClass
    _domainLockTable = {
        0: clusterlock.SafeLease,
        2: clusterlock.SafeLease,
        3: clusterlock.LocalLock,
        4: clusterlock.LocalLock,
        5: clusterlock.LocalLock,
    }

    # External leases support

    @classmethod
    def supports_external_leases(cls, version):
        return False


class LocalFsStorageDomain(fileSD.FileStorageDomain):
    manifestClass = LocalFsStorageDomainManifest

    supported_block_size = (sc.BLOCK_SIZE_512, sc.BLOCK_SIZE_4K)
    supported_alignment = (
        sc.ALIGNMENT_1M, sc.ALIGNMENT_2M, sc.ALIGNMENT_4M, sc.ALIGNMENT_8M)

    @property
    def supportsMailbox(self):
        return False

    @classmethod
    def _preCreateValidation(cls, sdUUID, domPath, typeSpecificArg, version):
        # Some trivial resource validation
        if os.path.abspath(typeSpecificArg) != typeSpecificArg:
            raise se.StorageDomainIllegalRemotePath(typeSpecificArg)

        fileSD.validateDirAccess(domPath)
        fileSD.validateFileSystemFeatures(sdUUID, domPath)

        sd.validateDomainVersion(version)

        # Make sure there are no remnants of other domain
        mdpat = os.path.join(domPath, "*", sd.DOMAIN_META_DATA)
        if len(glob(mdpat)) > 0:
            raise se.StorageDomainNotEmpty(typeSpecificArg)

    @classmethod
    def create(cls, sdUUID, domainName, domClass, remotePath, storageType,
               version, block_size=sc.BLOCK_SIZE_512,
               alignment=sc.ALIGNMENT_1M):
        """
        Create new storage domain

        Arguments:
            sdUUID (UUID): Storage Domain UUID
            domainName (str): Storage domain name
            domClass (int): Data/Iso
            remotePath (str): /data
            storageType (int): LOCALFS_DOMAIN
            version (int): DOMAIN_VERSIONS
            block_size (int): Underlying storage block size.
                Supported value is BLOCK_SIZE_512
            alignment (int): Sanlock alignment to use for this storage domain.
                Supported value is ALIGN_1M
        """
        cls.log.info("sdUUID=%s domainName=%s remotePath=%s "
                     "domClass=%s, block_size=%s, alignment=%s",
                     sdUUID, domainName, remotePath, domClass,
                     block_size, alignment)

        cls._validate_block_and_alignment(block_size, alignment, version)

        if not misc.isAscii(domainName) and not sd.supportsUnicode(version):
            raise se.UnicodeArgumentException()

        # Create local path
        mntPath = fileUtils.transformPath(remotePath)

        mntPoint = os.path.join(sc.REPO_MOUNT_DIR, mntPath)

        cls._preCreateValidation(sdUUID, mntPoint, remotePath, version)

        domainDir = os.path.join(mntPoint, sdUUID)
        cls._prepareMetadata(domainDir, sdUUID, domainName, domClass,
                             remotePath, storageType, version, alignment,
                             block_size)

        # create domain images folder
        imagesDir = os.path.join(domainDir, sd.DOMAIN_IMAGES)
        cls.log.info("Creating domain images directory %r", imagesDir)
        fileUtils.createdir(imagesDir)

        # create special imageUUID for ISO/Floppy volumes
        # Actually the local domain shouldn't be ISO, but
        # we can allow it for systems without NFS at all
        if domClass is sd.ISO_DOMAIN:
            isoDir = os.path.join(imagesDir, sd.ISO_IMAGE_UUID)
            cls.log.info("Creating ISO domain images directory %r", isoDir)
            fileUtils.createdir(isoDir)

        fsd = LocalFsStorageDomain(os.path.join(mntPoint, sdUUID))
        fsd.initSPMlease()

        return fsd

    @staticmethod
    def findDomainPath(sdUUID):
        for tmpSdUUID, domainPath in fileSD.scanDomains("_*"):
            if tmpSdUUID == sdUUID:
                return domainPath
        else:
            raise se.StorageDomainDoesNotExist(sdUUID)

    def getRealPath(self):
        return os.readlink(self.mountpoint)


def findDomain(sdUUID):
    return LocalFsStorageDomain(LocalFsStorageDomain.findDomainPath(sdUUID))
