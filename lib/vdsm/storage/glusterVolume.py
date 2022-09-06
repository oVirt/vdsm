# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import

from vdsm import utils
import vdsm.common.supervdsm as svdsm
from vdsm.storage import fileVolume
from vdsm.storage.sdc import sdCache

try:
    from vdsm.gluster.exception import GlusterException
    _glusterEnabled = True
except ImportError:
    _glusterEnabled = False

# Volume transport to Libvirt transport mapping
VOLUME_TRANS_MAP = {
    'TCP': 'tcp',
    'RDMA': 'rdma'
}


class GlusterVolume(fileVolume.FileVolume):

    def __init__(self, repoPath, sdUUID, imgUUID, volUUID):
        fileVolume.FileVolume.__init__(self, repoPath, sdUUID, imgUUID,
                                       volUUID)

    def getVmVolumeInfo(self):
        """
        Send info to represent Gluster volume as a network block device
        """
        rpath = sdCache.produce(self.sdUUID).getRealPath()
        volfileServer, volname = rpath.rsplit(":", 1)
        volname = volname.strip('/')

        # Extract the volume's transport using gluster cli
        svdsmProxy = svdsm.getProxy()

        try:
            res = svdsmProxy.glusterVolumeInfo(volname, volfileServer)
        except GlusterException:
            # In case of issues with finding transport type, default to tcp
            self.log.warning("Unable to find transport type for GlusterFS"
                             " volume %s. GlusterFS server = %s."
                             "Defaulting to tcp",
                             volname, volfileServer, exc_info=True)
            transport = VOLUME_TRANS_MAP['TCP']
            brickServers = []
        else:
            vol_info = res[volname]
            transport = VOLUME_TRANS_MAP[vol_info['transportType'][0]]
            brickServers = utils.unique(
                brick.split(":", 1)[0]
                for brick in vol_info['bricks']
            )
            # remove server passed as argument from backup servers to avoid
            # duplicates
            if volfileServer in brickServers:
                brickServers.remove(volfileServer)

        # gfapi does not use brick ports, it uses the glusterd port (24007)
        # from the hosts passed to fetch the volume information.
        # If 0 is passed, gfapi defaults to 24007.
        volPort = "0"

        imgFilePath = self.getVolumePath()
        imgFilePath_list = imgFilePath.rsplit("/")

        # Extract path to the image, relative to the gluster mount
        imgFileRelPath = "/".join(imgFilePath_list[-4:])

        glusterPath = volname + '/' + imgFileRelPath

        hosts = [dict(name=volfileServer,
                      port=volPort,
                      transport=transport)]
        hosts.extend(dict(name=brickServer, port=volPort, transport=transport)
                     for brickServer in brickServers)

        return {
            'type': 'network',
            'path': glusterPath,
            'protocol': 'gluster',
            'hosts': hosts,
        }
