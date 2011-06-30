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

from config import config
import logging

import sdc
import sd
import storage_exception as se


class StorageDomainFactory:
    log = logging.getLogger("Storage.StorageDomainFactory")
    storage_repository = config.get('irs', 'repository')
    __sdc = sdc.StorageDomainCache(storage_repository)

    @classmethod
    def manuallyRemoveDomain(cls, sdUUID):
        cls.__sdc.manuallyRemoveDomain(sdUUID)

    @classmethod
    def produce(cls, sdUUID):
        """
        Produce a new Storage domain
        """

        newSD = cls.__sdc.lookup(sdUUID)
        if not newSD:
            raise se.StorageDomainDoesNotExist(sdUUID)
        return newSD


    @classmethod
    def create(cls, sdUUID, storageType, domainName, domClass, typeSpecificArg, version):
        """
        Create a new Storage domain
        """
        import nfsSD
        import localFsSD
        import blockSD

        newSD = None
        if storageType in [sd.NFS_DOMAIN]:
            newSD = nfsSD.NfsStorageDomain.create(sdUUID=sdUUID,
                domainName=domainName, domClass=domClass,
                remotePath=typeSpecificArg, storageType=storageType,
                version=version)
        elif storageType in [sd.LOCALFS_DOMAIN]:
            newSD = localFsSD.LocalFsStorageDomain.create(sdUUID=sdUUID,
                domainName=domainName, domClass=domClass,
                remotePath=typeSpecificArg, storageType=storageType,
                version=version)
        elif storageType in [sd.ISCSI_DOMAIN, sd.FCP_DOMAIN]:
            newSD = blockSD.BlockStorageDomain.create(sdUUID=sdUUID,
                domainName=domainName, domClass=domClass,
                vgUUID=typeSpecificArg, storageType=storageType,
                version=version)
        else:
            raise se.StorageDomainTypeError(storageType)

        cls.__sdc.manuallyAddDomain(newSD)
        return newSD


    @classmethod
    def getAllUUIDs(cls):
        return cls.__sdc.getUUIDs()


    @classmethod
    def refresh(cls):
        cls.__sdc.refresh()


    @classmethod
    def invalidateStorage(cls):
        cls.__sdc.invalidateStorage()


    @classmethod
    def refreshStorage(cls):
        cls.__sdc.refreshStorage()

