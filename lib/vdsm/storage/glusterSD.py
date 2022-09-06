# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import

import os

from vdsm.common.config import config

from vdsm.storage import constants as sc
from vdsm.storage import exception as se
from vdsm.storage import fileSD
from vdsm.storage import glusterVolume
from vdsm.storage import mount
from vdsm.storage import nfsSD
from vdsm.storage import sd


class GlusterStorageDomain(nfsSD.NfsStorageDomain):

    if config.getboolean("gluster", "enable_4k_storage"):
        supported_block_size = (
            sc.BLOCK_SIZE_AUTO, sc.BLOCK_SIZE_512, sc.BLOCK_SIZE_4K)

    @classmethod
    def getMountPoint(cls, mountPath):
        return os.path.join(sc.REPO_MOUNT_DIR, sd.GLUSTERSD_DIR, mountPath)

    def getVolumeClass(self):
        return glusterVolume.GlusterVolume

    @staticmethod
    def findDomainPath(sdUUID):
        glusterDomPath = os.path.join(sd.GLUSTERSD_DIR, "*")
        for tmpSdUUID, domainPath in fileSD.scanDomains(glusterDomPath):
            if tmpSdUUID == sdUUID:
                mountpoint = os.path.dirname(domainPath)
                if mount.isMounted(mountpoint):
                    return domainPath

        raise se.StorageDomainDoesNotExist(sdUUID)


def findDomain(sdUUID):
    return GlusterStorageDomain(GlusterStorageDomain.findDomainPath(sdUUID))
