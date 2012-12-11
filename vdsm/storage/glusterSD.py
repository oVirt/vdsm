import os
import nfsSD
import sd
import glusterVolume
import fileSD
import mount
import storage_exception as se


class GlusterStorageDomain(nfsSD.NfsStorageDomain):

    @classmethod
    def getMountPoint(cls, mountPath):
        return os.path.join(cls.storage_repository,
                            sd.DOMAIN_MNT_POINT, sd.GLUSTERSD_DIR, mountPath)

    def getVolumeClass(self):
        return glusterVolume.GlusterVolume

    @staticmethod
    def findDomainPath(sdUUID):
        glusterDomPath = os.path.join(sd.GLUSTERSD_DIR, "*")
        for tmpSdUUID, domainPath in fileSD.scanDomains(glusterDomPath):
            if tmpSdUUID == sdUUID and mount.isMounted(os.path.join(domainPath,
                                                       "..")):
                return domainPath

        raise se.StorageDomainDoesNotExist(sdUUID)


def findDomain(sdUUID):
    return GlusterStorageDomain(GlusterStorageDomain.findDomainPath(sdUUID))
