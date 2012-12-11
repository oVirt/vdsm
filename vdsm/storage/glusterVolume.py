from volume import VmVolumeInfo
import fileVolume
from sdc import sdCache
import supervdsm as svdsm


class GlusterVolume(fileVolume.FileVolume):

    def __init__(self, repoPath, sdUUID, imgUUID, volUUID):
        fileVolume.FileVolume.__init__(self, repoPath, sdUUID, imgUUID,
                                       volUUID)

    def getVmVolumeInfo(self):
        """
        Send info to represent Gluster volume as a network block device
        """
        rpath = sdCache.produce(self.sdUUID).getRemotePath()
        rpath_list = rpath.rsplit(":", 1)
        volfileServer = rpath_list[0]
        volname = rpath_list[1]

        # Volume transport to Libvirt transport mapping
        VOLUME_TRANS_MAP = {'TCP': 'tcp', 'RDMA': 'rdma'}

        # Extract the volume's transport using gluster cli
        svdsmProxy = svdsm.getProxy()
        volInfo = svdsmProxy.glusterVolumeInfo(volname)
        volTrans = VOLUME_TRANS_MAP[volInfo[volname]['transportType'][0]]

        # Use default port
        volPort = "0"

        imgFilePath = self.getVolumePath()
        imgFilePath_list = imgFilePath.rsplit("/")

        # Extract path to the image, relative to the gluster mount
        imgFileRelPath = "/".join(imgFilePath_list[-4:])

        glusterPath = volname + '/' + imgFileRelPath

        return {'volType': VmVolumeInfo.TYPE_NETWORK, 'path': glusterPath,
                'protocol': 'gluster', 'volPort': volPort,
                'volTransport': volTrans,
                'volfileServer': volfileServer}
