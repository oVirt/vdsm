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

from vdsm.storage import localFsSD
from vdsm.storage import constants as sc
from vdsm.storage import exception as se
from vdsm.storage import fileSD
from vdsm.storage import sd


def test_incorrect_block_rejected():
    with pytest.raises(se.InvalidParameterException):
        localFsSD.LocalFsStorageDomain.create(
            sc.BLANK_UUID, "test", sd.DATA_DOMAIN,
            sc.BLANK_UUID, sd.ISCSI_DOMAIN, 4, sc.BLOCK_SIZE_4K, sc.ALIGN_1M)


def test_incorrect_alignment_rejected():
    with pytest.raises(se.InvalidParameterException):
        localFsSD.LocalFsStorageDomain.create(
            sc.BLANK_UUID, "test", sd.DATA_DOMAIN,
            sc.BLANK_UUID, sd.ISCSI_DOMAIN, 4, sc.BLOCK_SIZE_512, sc.ALIGN_2M)


@pytest.mark.parametrize("domain_version", [3, 4])
def test_create_domain_metadata(tmpdir, tmp_repo, fake_access, domain_version):
    remote_path = str(tmpdir.mkdir("domain"))
    tmp_repo.connect_localfs(remote_path)

    sd_uuid = str(uuid.uuid4())
    domain_name = "domain"

    dom = localFsSD.LocalFsStorageDomain.create(
        sdUUID=sd_uuid,
        domainName=domain_name,
        domClass=sd.DATA_DOMAIN,
        remotePath=remote_path,
        version=domain_version,
        storageType=sd.LOCALFS_DOMAIN,
        block_size=sc.BLOCK_SIZE_512,
        alignment=sc.ALIGN_1M)

    lease = sd.DEFAULT_LEASE_PARAMS
    assert dom.getMetadata() == {
        sd.DMDK_CLASS: sd.DATA_DOMAIN,
        sd.DMDK_DESCRIPTION: domain_name,
        sd.DMDK_IO_OP_TIMEOUT_SEC: lease[sd.DMDK_IO_OP_TIMEOUT_SEC],
        sd.DMDK_LEASE_RETRIES: lease[sd.DMDK_LEASE_RETRIES],
        sd.DMDK_LEASE_TIME_SEC: lease[sd.DMDK_LEASE_TIME_SEC],
        sd.DMDK_LOCK_POLICY: "",
        sd.DMDK_LOCK_RENEWAL_INTERVAL_SEC:
            lease[sd.DMDK_LOCK_RENEWAL_INTERVAL_SEC],
        sd.DMDK_POOLS: [],
        sd.DMDK_ROLE: sd.REGULAR_DOMAIN,
        sd.DMDK_SDUUID: sd_uuid,
        sd.DMDK_TYPE: sd.LOCALFS_DOMAIN,
        sd.DMDK_VERSION: domain_version,
        fileSD.REMOTE_PATH: remote_path
    }
