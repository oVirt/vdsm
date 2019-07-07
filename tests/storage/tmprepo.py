# Copyright 2019 Red Hat, Inc.
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
from __future__ import division

import os
import uuid

from vdsm.storage import constants as sc
from vdsm.storage import fileUtils
from vdsm.storage import localFsSD
from vdsm.storage import sd
from vdsm.storage.sdc import sdCache


class TemporaryRepo(object):
    """
    Temporary storage repository replacing /rhev/data-center during tests.
    """

    def __init__(self, tmpdir, tmp_fs):
        self.tmpdir = tmpdir
        self.tmp_fs = tmp_fs

        # Create rhev/data-center directory in the tmpdir, so we don't mix
        # temporary files created by the same test in the data-center.
        self.path = str(self.tmpdir.mkdir("rhev").mkdir("data-center"))
        self.pool_id = str(uuid.uuid4())
        self.pool_dir = os.path.join(self.path, self.pool_id)
        self.mnt_dir = os.path.join(self.path, sc.DOMAIN_MNT_POINT)

        # TODO: Should we create pool_dir now?
        os.makedirs(self.mnt_dir)

    def connect_localfs(self, remote_path):
        """
        Connect a local directory to repository.
        """
        local_path = fileUtils.transformPath(remote_path)
        dom_link = os.path.join(self.mnt_dir, local_path)
        os.symlink(remote_path, dom_link)

    def disconnect_localfs(self, remote_path):
        """
        Disconnect a local directory from the repository.
        """
        local_path = fileUtils.transformPath(remote_path)
        dom_link = os.path.join(self.mnt_dir, local_path)
        os.remove(dom_link)

    def create_localfs_domain(
            self, name, version, block_size=sc.BLOCK_SIZE_512,
            alignment=sc.ALIGNMENT_1M, filesystem=None, remote_path=None):
        """
        Create local FS file storage domain in the repository.

        If filesystem argument is provided, new file system on loopback device
        is created and used as local FS for creating new domain.

        If remote_path is provided, use existing directory to create the
        storage domain. The block size must match the underlying block size of
        the existing directory.
        """
        if remote_path is None:
            remote_path = str(self.tmpdir.mkdir(name))

        if filesystem is None:
            self.connect_localfs(remote_path)
        else:
            self._connect_loopbackfs(remote_path, filesystem)

        domain = self._create_domain(
            name, version, remote_path, block_size=block_size,
            alignment=alignment)
        # To make sure we read metadata from storage in tests, invalidate
        # metadata now.
        domain.invalidateMetadata()

        return domain

    def _connect_loopbackfs(self, remote_path, filesystem):
        """
        Create loopback device of size `size`, create file system on top of it
        and mount it to `remote_path`.
        """
        self.tmp_fs.create_filesystem(filesystem, remote_path)
        self.connect_localfs(remote_path)

    def _disconnect_loopbackfs(self, remote_path):
        """
        Perform umount of loopback device and destroy the device.
        """
        self.disconnect_localfs(remote_path)
        self.tmp_fs.destroy_filesystem(remote_path)

    def _create_domain(
            self, name, version, remote_path, block_size=sc.BLOCK_SIZE_512,
            alignment=sc.ALIGNMENT_1M):
        sd_uuid = str(uuid.uuid4())

        dom = localFsSD.LocalFsStorageDomain.create(
            sdUUID=sd_uuid,
            domainName=name,
            domClass=sd.DATA_DOMAIN,
            remotePath=remote_path,
            version=version,
            storageType=sd.LOCALFS_DOMAIN,
            block_size=block_size,
            alignment=alignment)

        sdCache.knownSDs[sd_uuid] = localFsSD.findDomain
        sdCache.manuallyAddDomain(dom)

        # sd.StorageDomainManifest.getRepoPath() assumes at least one pool is
        # attached
        dom.attach(self.pool_id)

        return dom
