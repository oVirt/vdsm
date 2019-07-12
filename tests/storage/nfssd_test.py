#
# Copyright 2014-2018 Red Hat, Inc.
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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA
# 02110-1301  USA
#
# Refer to the README and COPYING files for full details of the license
#

from __future__ import absolute_import
from __future__ import division

import uuid

import pytest

from vdsm.storage import nfsSD
from vdsm.storage import constants as sc
from vdsm.storage import exception as se
from vdsm.storage import sd


@pytest.mark.parametrize("version", [3, 4])
@pytest.mark.parametrize("block_size", [sc.BLOCK_SIZE_4K, sc.BLOCK_SIZE_AUTO])
def test_incorrect_block_rejected(version, block_size):
    # Note: assumes that validation is done before trying to reach storage.
    with pytest.raises(se.InvalidParameterException):
        nfsSD.NfsStorageDomain.create(
            sdUUID=str(uuid.uuid4()),
            domainName="test",
            domClass=sd.DATA_DOMAIN,
            remotePath="server:/path",
            version=version,
            storageType=sd.NFS_DOMAIN,
            block_size=block_size)
