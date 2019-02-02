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

from vdsm.storage import constants as sc
from vdsm.storage import fileUtils


class TemporaryRepo(object):
    """
    Temporary storage repository replacing /rhev/data-center during tests.
    """

    def __init__(self, path, pool_id):
        self.path = path
        self.pool_id = pool_id
        self.pool_dir = os.path.join(path, pool_id)
        self.mnt_dir = os.path.join(path, sc.DOMAIN_MNT_POINT)

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
