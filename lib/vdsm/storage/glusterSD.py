#
# Copyright 2012-2017 Red Hat, Inc.
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
