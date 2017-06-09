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

import vdsm.supervdsm as svdsm
from vdsm.storage import fileVolume
from vdsm.storage.sdc import sdCache
from vdsm.storage.volume import VmVolumeInfo
try:
    from vdsm.gluster.exception import GlusterException
    _glusterEnabled = True
except ImportError:
    _glusterEnabled = False


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

        # Volume transport to Libvirt transport mapping
        VOLUME_TRANS_MAP = {'TCP': 'tcp', 'RDMA': 'rdma'}

        # Extract the volume's transport using gluster cli
        svdsmProxy = svdsm.getProxy()

        try:
            volInfo = svdsmProxy.glusterVolumeInfo(volname, volfileServer)
            volTrans = VOLUME_TRANS_MAP[volInfo[volname]['transportType'][0]]
        except GlusterException:
            # In case of issues with finding transport type, default to tcp
            self.log.warning("Unable to find transport type for GlusterFS"
                             " volume %s. GlusterFS server = %s."
                             "Defaulting to tcp",
                             volname, volfileServer, exc_info=True)
            volTrans = VOLUME_TRANS_MAP['TCP']

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
