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

import os
import stat
import time
import uuid

import pytest

from vdsm.constants import GIB
from vdsm.constants import MEGAB
from vdsm.storage import localFsSD
from vdsm.storage import constants as sc
from vdsm.storage import exception as se
from vdsm.storage import fileSD
from vdsm.storage import qemuimg
from vdsm.storage import sd
from vdsm.storage.sdc import sdCache

from . import qemuio
from . marks import xfail_python3

PREALLOCATED_VOL_SIZE = 10 * MEGAB
SPARSE_VOL_SIZE = GIB
INITIAL_VOL_SIZE = 1 * MEGAB


def test_incorrect_block_rejected():
    with pytest.raises(se.InvalidParameterException):
        localFsSD.LocalFsStorageDomain.create(
            sc.BLANK_UUID, "test", sd.DATA_DOMAIN,
            sc.BLANK_UUID, sd.ISCSI_DOMAIN, 4, sc.BLOCK_SIZE_4K,
            sc.ALIGNMENT_1M)


def test_incorrect_alignment_rejected():
    with pytest.raises(se.InvalidParameterException):
        localFsSD.LocalFsStorageDomain.create(
            sc.BLANK_UUID, "test", sd.DATA_DOMAIN,
            sc.BLANK_UUID, sd.ISCSI_DOMAIN, 4, sc.BLOCK_SIZE_512,
            sc.ALIGNMENT_2M)


@pytest.mark.parametrize("version", [3, 4])
@pytest.mark.parametrize("block_size", [sc.BLOCK_SIZE_512, sc.BLOCK_SIZE_4K])
@pytest.mark.parametrize(
    "alignment", [sc.ALIGNMENT_1M, sc.ALIGNMENT_2M, sc.ALIGNMENT_4M,
                  sc.ALIGNMENT_8M])
def test_incorrect_version_and_block_rejected(version, block_size, alignment):
    # block size 512b and alignment of 1M is the only allowed combination for
    # storage domain 3 and 4
    if block_size != sc.BLOCK_SIZE_512 and alignment != sc.ALIGNMENT_1M:
        with pytest.raises(se.InvalidParameterException):
            localFsSD.LocalFsStorageDomain.create(
                sc.BLANK_UUID, "test", sd.DATA_DOMAIN,
                sc.BLANK_UUID, sd.ISCSI_DOMAIN, version, block_size, alignment)


@xfail_python3
@pytest.mark.parametrize("domain_version", [3, 4, 5])
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
        alignment=sc.ALIGNMENT_1M)

    sdCache.knownSDs[sd_uuid] = localFsSD.findDomain
    sdCache.manuallyAddDomain(dom)

    lease = sd.DEFAULT_LEASE_PARAMS
    expected = {
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

    # In version 5 we added ALIGNMENT and BLOCK_SIZE.
    if domain_version > 4:
        expected[sd.DMDK_ALIGNMENT] = sc.ALIGNMENT_1M
        expected[sd.DMDK_BLOCK_SIZE] = sc.BLOCK_SIZE_512

    # Tests also alignment and block size properties here.
    assert dom.alignment == sc.ALIGNMENT_1M
    assert dom.block_size == sc.BLOCK_SIZE_512

    actual = dom.getMetadata()

    assert expected == actual


@xfail_python3
@pytest.mark.parametrize("domain_version", [3, 4, 5])
def test_domain_lease(tmpdir, tmp_repo, fake_access, domain_version):
    dom = tmp_repo.create_localfs_domain(name="domain", version=domain_version)
    lease = dom.getClusterLease()
    assert lease.name == "SDM"
    assert lease.path == dom.getLeasesFilePath()
    assert lease.offset == dom.alignment


@xfail_python3
def test_volume_life_cycle(monkeypatch, tmpdir, tmp_repo, fake_access,
                           fake_rescan, tmp_db, fake_task):
    # as creation of block storage domain and volume is quite time consuming,
    # we test several volume operations in one test to speed up the test suite
    dom = tmp_repo.create_localfs_domain(name="domain", version=4)

    img_uuid = str(uuid.uuid4())
    vol_uuid = str(uuid.uuid4())
    vol_capacity = 10 * 1024**3
    vol_size_blk = vol_capacity // sc.BLOCK_SIZE_512
    vol_desc = "Test volume"

    with monkeypatch.context() as mc:
        mc.setattr(time, "time", lambda: 1550522547)
        dom.createVolume(
            imgUUID=img_uuid,
            size_blk=vol_size_blk,
            volFormat=sc.COW_FORMAT,
            preallocate=sc.SPARSE_VOL,
            diskType=sc.DATA_DISKTYPE,
            volUUID=vol_uuid,
            desc=vol_desc,
            srcImgUUID=sc.BLANK_UUID,
            srcVolUUID=sc.BLANK_UUID)

    # test create volume
    vol = dom.produceVolume(img_uuid, vol_uuid)
    actual = vol.getInfo()

    assert int(actual["capacity"]) == vol_capacity
    assert int(actual["ctime"]) == 1550522547
    assert actual["description"] == vol_desc
    assert actual["disktype"] == "DATA"
    assert actual["domain"] == dom.sdUUID
    assert actual["format"] == sc.VOLUME_TYPES[sc.COW_FORMAT]
    assert actual["parent"] == sc.BLANK_UUID
    assert actual["status"] == "OK"
    assert actual["type"] == "SPARSE"
    assert actual["voltype"] == "LEAF"
    assert actual["uuid"] == vol_uuid

    vol_path = vol.getVolumePath()

    qcow2_info = qemuimg.info(vol_path)

    assert qcow2_info["actualsize"] < vol_capacity
    assert qcow2_info["virtualsize"] == vol_capacity

    # test volume prepare, teardown does nothing in case of file volume
    vol.prepare()

    permissions = stat.S_IMODE(os.stat(vol_path).st_mode)
    assert permissions == sc.FILE_VOLUME_PERMISSIONS

    # verify we can really write and read to an image
    qemuio.write_pattern(vol_path, "qcow2")
    qemuio.verify_pattern(vol_path, "qcow2")

    # test deleting of the volume - check volume and metadata files are
    # deleted once the volume is deleted. Lock files is not checked as it's
    # not created in case of file volume which uses LocalLock
    vol_path = vol.getVolumePath()
    meta_path = vol._manifest.metaVolumePath(vol_path)

    assert os.path.isfile(vol_path)
    assert os.path.isfile(meta_path)

    vol.delete(postZero=False, force=False, discard=False)

    assert not os.path.isfile(vol_path)
    assert not os.path.isfile(meta_path)


@xfail_python3
@pytest.mark.parametrize("domain_version", [4, 5])
def test_volume_metadata(tmpdir, tmp_repo, fake_access, fake_rescan, tmp_db,
                         fake_task, domain_version):
    dom = tmp_repo.create_localfs_domain(name="domain", version=domain_version)

    img_uuid = str(uuid.uuid4())
    vol_uuid = str(uuid.uuid4())

    dom.createVolume(
        desc="old description",
        diskType=sc.DATA_DISKTYPE,
        imgUUID=img_uuid,
        preallocate=sc.SPARSE_VOL,
        size_blk=10 * 1024 ** 3 // sc.BLOCK_SIZE_512,
        srcImgUUID=sc.BLANK_UUID,
        srcVolUUID=sc.BLANK_UUID,
        volFormat=sc.COW_FORMAT,
        volUUID=vol_uuid)

    vol = dom.produceVolume(img_uuid, vol_uuid)
    meta_path = vol.getMetaVolumePath()

    # Check capacity
    assert 10 * 1024 ** 3 == vol.getCapacity()
    vol.setCapacity(0)
    with pytest.raises(se.MetaDataValidationError):
        vol.getCapacity()
    vol.setCapacity(10 * 1024 ** 3)

    # Change volume metadata.
    md = vol.getMetadata()
    md.description = "new description"
    vol.setMetadata(md)
    with open(meta_path) as f:
        data = f.read()
    assert data == md.storage_format(domain_version)

    # Test overriding with new keys.
    md = vol.getMetadata()
    vol.setMetadata(md, CAP=md.capacity)
    with open(meta_path) as f:
        data = f.read()
    assert data == md.storage_format(domain_version, CAP=md.capacity)


@xfail_python3
@pytest.mark.parametrize("domain_version", [4, 5])
def test_volume_create_raw_prealloc(
        tmpdir, tmp_repo, fake_access, fake_rescan, tmp_db, fake_task,
        local_fallocate, domain_version):
    dom = tmp_repo.create_localfs_domain(name="domain", version=domain_version)

    img_uuid = str(uuid.uuid4())
    vol_uuid = str(uuid.uuid4())

    dom.createVolume(
        imgUUID=img_uuid,
        size_blk=PREALLOCATED_VOL_SIZE // sc.BLOCK_SIZE_512,
        volFormat=sc.RAW_FORMAT,
        preallocate=sc.PREALLOCATED_VOL,
        diskType='DATA',
        volUUID=vol_uuid,
        desc="Test volume",
        srcImgUUID=sc.BLANK_UUID,
        srcVolUUID=sc.BLANK_UUID)

    vol = dom.produceVolume(img_uuid, vol_uuid)

    path = vol.getVolumePath()
    qemu_info = qemuimg.info(path)

    verify_volume_file(
        path=path,
        format=qemuimg.FORMAT.RAW,
        virtual_size=PREALLOCATED_VOL_SIZE,
        qemu_info=qemu_info)

    assert qemu_info['actualsize'] == PREALLOCATED_VOL_SIZE

    # Verify actual volume metadata
    actual = vol.getInfo()
    assert int(actual["capacity"]) == PREALLOCATED_VOL_SIZE
    assert actual["format"] == "RAW"
    assert actual["type"] == "PREALLOCATED"
    assert actual["voltype"] == "LEAF"
    assert actual["uuid"] == vol_uuid
    assert int(actual["apparentsize"]) == qemu_info['virtualsize']
    assert int(actual["truesize"]) == qemu_info['actualsize']


@xfail_python3
@pytest.mark.parametrize("domain_version", [4, 5])
@pytest.mark.parametrize("initial_size", [0, INITIAL_VOL_SIZE])
def test_volume_create_raw_prealloc_with_initial_size(
        tmpdir, tmp_repo, tmp_db, fake_access, fake_rescan,
        fake_task, local_fallocate, initial_size, domain_version):
    dom = tmp_repo.create_localfs_domain(name="domain", version=domain_version)

    img_uuid = str(uuid.uuid4())
    vol_uuid = str(uuid.uuid4())

    dom.createVolume(
        imgUUID=img_uuid,
        size_blk=PREALLOCATED_VOL_SIZE // sc.BLOCK_SIZE_512,
        volFormat=sc.RAW_FORMAT,
        preallocate=sc.PREALLOCATED_VOL,
        diskType='DATA',
        volUUID=vol_uuid,
        desc="Test volume",
        srcImgUUID=sc.BLANK_UUID,
        srcVolUUID=sc.BLANK_UUID,
        initial_size_blk=initial_size // sc.BLOCK_SIZE_512)

    vol = dom.produceVolume(img_uuid, vol_uuid)

    path = vol.getVolumePath()
    qemu_info = qemuimg.info(path)

    verify_volume_file(
        path=path,
        format=qemuimg.FORMAT.RAW,
        virtual_size=PREALLOCATED_VOL_SIZE,
        qemu_info=qemu_info)

    assert qemu_info['actualsize'] == initial_size

    # Verify actual volume metadata
    actual = vol.getInfo()
    assert int(actual["capacity"]) == PREALLOCATED_VOL_SIZE
    assert actual["format"] == "RAW"
    assert actual["type"] == "PREALLOCATED"
    assert actual["voltype"] == "LEAF"
    assert actual["uuid"] == vol_uuid
    assert int(actual["apparentsize"]) == qemu_info['virtualsize']
    assert int(actual["truesize"]) == qemu_info['actualsize']


@xfail_python3
@pytest.mark.parametrize("domain_version", [4, 5])
@pytest.mark.parametrize("vol_format,prealloc", [
    (sc.RAW_FORMAT, sc.SPARSE_VOL),
    (sc.COW_FORMAT, sc.PREALLOCATED_VOL),
    (sc.COW_FORMAT, sc.SPARSE_VOL),
])
def test_volume_create_initial_size_not_supported(
        tmpdir, tmp_repo, tmp_db, fake_access, fake_task, local_fallocate,
        fake_rescan, vol_format, prealloc, domain_version):
    dom = tmp_repo.create_localfs_domain(name="domain", version=domain_version)

    img_uuid = str(uuid.uuid4())
    vol_uuid = str(uuid.uuid4())

    with pytest.raises(se.VolumeCreationError):
        dom.createVolume(
            imgUUID=img_uuid,
            size_blk=SPARSE_VOL_SIZE // sc.BLOCK_SIZE_512,
            volFormat=vol_format,
            preallocate=prealloc,
            diskType='DATA',
            volUUID=vol_uuid,
            desc="Test volume",
            srcImgUUID=sc.BLANK_UUID,
            srcVolUUID=sc.BLANK_UUID,
            initial_size_blk=INITIAL_VOL_SIZE // sc.BLOCK_SIZE_512)


@xfail_python3
@pytest.mark.parametrize("domain_version", [4, 5])
def test_volume_create_raw_sparse(
        tmpdir, tmp_repo, fake_access, fake_rescan, tmp_db,
        fake_task, local_fallocate, domain_version):
    dom = tmp_repo.create_localfs_domain(name="domain", version=domain_version)

    img_uuid = str(uuid.uuid4())
    vol_uuid = str(uuid.uuid4())

    dom.createVolume(
        imgUUID=img_uuid,
        size_blk=SPARSE_VOL_SIZE // sc.BLOCK_SIZE_512,
        volFormat=sc.RAW_FORMAT,
        preallocate=sc.SPARSE_VOL,
        diskType='DATA',
        volUUID=vol_uuid,
        desc="Test volume",
        srcImgUUID=sc.BLANK_UUID,
        srcVolUUID=sc.BLANK_UUID)

    vol = dom.produceVolume(img_uuid, vol_uuid)

    path = vol.getVolumePath()
    qemu_info = qemuimg.info(path)

    verify_volume_file(
        path=path,
        format=qemuimg.FORMAT.RAW,
        virtual_size=SPARSE_VOL_SIZE,
        qemu_info=qemu_info)

    assert qemu_info['actualsize'] == 0

    # Verify actual volume metadata
    actual = vol.getInfo()
    assert int(actual["capacity"]) == SPARSE_VOL_SIZE
    assert actual["format"] == "RAW"
    assert actual["type"] == "SPARSE"
    assert actual["voltype"] == "LEAF"
    assert actual["uuid"] == vol_uuid
    assert int(actual["apparentsize"]) == qemu_info['virtualsize']
    assert int(actual["truesize"]) == qemu_info['actualsize']


@xfail_python3
@pytest.mark.parametrize("domain_version", [4, 5])
def test_volume_create_cow_sparse(
        tmpdir, tmp_repo, fake_access, fake_rescan, tmp_db,
        fake_task, local_fallocate, domain_version):
    dom = tmp_repo.create_localfs_domain(name="domain", version=domain_version)

    img_uuid = str(uuid.uuid4())
    vol_uuid = str(uuid.uuid4())

    dom.createVolume(
        imgUUID=img_uuid,
        size_blk=SPARSE_VOL_SIZE // sc.BLOCK_SIZE_512,
        volFormat=sc.COW_FORMAT,
        preallocate=sc.SPARSE_VOL,
        diskType='DATA',
        volUUID=vol_uuid,
        desc="Test volume",
        srcImgUUID=sc.BLANK_UUID,
        srcVolUUID=sc.BLANK_UUID)

    vol = dom.produceVolume(img_uuid, vol_uuid)

    path = vol.getVolumePath()
    qemu_info = qemuimg.info(path)

    verify_volume_file(
        path=path,
        format=qemuimg.FORMAT.QCOW2,
        virtual_size=SPARSE_VOL_SIZE,
        qemu_info=qemu_info)

    # Check the volume specific actual size is fragile,
    # will easily break on CI or when qemu change the implementation.
    assert qemu_info['actualsize'] < MEGAB

    # Verify actual volume metadata
    actual = vol.getInfo()
    assert int(actual["capacity"]) == SPARSE_VOL_SIZE
    assert actual["format"] == "COW"
    assert actual["type"] == "SPARSE"
    assert actual["voltype"] == "LEAF"
    assert actual["uuid"] == vol_uuid
    # Check the volume specific apparent size is fragile,
    # will easily break on CI or when qemu change the implementation.
    assert int(actual["apparentsize"]) < MEGAB
    assert int(actual["truesize"]) == qemu_info['actualsize']


@xfail_python3
@pytest.mark.parametrize("domain_version", [4, 5])
def test_volume_create_cow_sparse_with_parent(
        tmpdir, tmp_repo, fake_access, fake_rescan, tmp_db,
        fake_task, local_fallocate, domain_version):
    dom = tmp_repo.create_localfs_domain(name="domain", version=domain_version)

    parent_img_uuid = str(uuid.uuid4())
    parent_vol_uuid = str(uuid.uuid4())

    dom.createVolume(
        imgUUID=parent_img_uuid,
        size_blk=SPARSE_VOL_SIZE // sc.BLOCK_SIZE_512,
        volFormat=sc.RAW_FORMAT,
        preallocate=sc.SPARSE_VOL,
        diskType='DATA',
        volUUID=parent_vol_uuid,
        desc="Test volume",
        srcImgUUID=sc.BLANK_UUID,
        srcVolUUID=sc.BLANK_UUID)

    parent_vol = dom.produceVolume(parent_img_uuid, parent_vol_uuid)
    parent_vol.setShared()

    vol_uuid = str(uuid.uuid4())

    dom.createVolume(
        imgUUID=parent_img_uuid,
        size_blk=SPARSE_VOL_SIZE // sc.BLOCK_SIZE_512,
        volFormat=sc.COW_FORMAT,
        preallocate=sc.SPARSE_VOL,
        diskType='DATA',
        volUUID=vol_uuid,
        desc="Test volume",
        srcImgUUID=parent_vol.imgUUID,
        srcVolUUID=parent_vol.volUUID)
    vol = dom.produceVolume(parent_img_uuid, vol_uuid)

    path = vol.getVolumePath()
    qemu_info = qemuimg.info(path)

    verify_volume_file(
        path=path,
        format=qemuimg.FORMAT.QCOW2,
        virtual_size=SPARSE_VOL_SIZE,
        qemu_info=qemu_info,
        backing_file=parent_vol.volUUID)

    # Check the volume specific actual size is fragile,
    # will easily break on CI or when qemu change the implementation.
    assert qemu_info['actualsize'] < MEGAB

    # Verify actual volume metadata
    actual = vol.getInfo()
    assert int(actual["capacity"]) == SPARSE_VOL_SIZE
    assert actual["format"] == "COW"
    assert actual["type"] == "SPARSE"
    assert actual["voltype"] == "LEAF"
    assert actual["uuid"] == vol_uuid
    # Check the volume specific apparent size is fragile,
    # will easily break on CI or when qemu change the implementation.
    assert int(actual["apparentsize"]) < MEGAB
    assert int(actual["truesize"]) == qemu_info['actualsize']


@xfail_python3
@pytest.mark.parametrize("initial_size", [
    -1,
    (PREALLOCATED_VOL_SIZE // sc.BLOCK_SIZE_512) + 1
])
def test_volume_create_raw_prealloc_invalid_initial_size(
        tmpdir, tmp_repo, tmp_db, fake_access, fake_task, local_fallocate,
        fake_rescan, initial_size):
    dom = tmp_repo.create_localfs_domain(name="domain", version=5)

    img_uuid = str(uuid.uuid4())
    vol_uuid = str(uuid.uuid4())

    with pytest.raises(se.VolumeCreationError):
        dom.createVolume(
            imgUUID=img_uuid,
            size_blk=PREALLOCATED_VOL_SIZE // sc.BLOCK_SIZE_512,
            volFormat=sc.RAW_FORMAT,
            preallocate=sc.PREALLOCATED_VOL,
            diskType='DATA',
            volUUID=vol_uuid,
            desc="Test volume",
            srcImgUUID=sc.BLANK_UUID,
            srcVolUUID=sc.BLANK_UUID,
            initial_size_blk=initial_size)


@xfail_python3
@pytest.mark.parametrize("domain_version", [4, 5])
def test_create_snapshot_size(
        tmpdir, tmp_repo, fake_access, fake_rescan, tmp_db,
        fake_task, local_fallocate, domain_version):
    dom = tmp_repo.create_localfs_domain(name="domain", version=domain_version)

    img_uuid = str(uuid.uuid4())
    parent_vol_uuid = str(uuid.uuid4())
    parent_vol_capacity = SPARSE_VOL_SIZE
    vol_uuid = str(uuid.uuid4())
    vol_capacity = 2 * parent_vol_capacity

    # Create parent volume.

    dom.createVolume(
        imgUUID=img_uuid,
        size_blk=parent_vol_capacity // sc.BLOCK_SIZE_512,
        volFormat=sc.RAW_FORMAT,
        preallocate=sc.SPARSE_VOL,
        diskType='DATA',
        volUUID=parent_vol_uuid,
        desc="Test parent volume",
        srcImgUUID=sc.BLANK_UUID,
        srcVolUUID=sc.BLANK_UUID)

    parent_vol = dom.produceVolume(img_uuid, parent_vol_uuid)

    # Verify that snapshot cannot be smaller than the parent.

    with pytest.raises(se.InvalidParameterException):
        dom.createVolume(
            imgUUID=img_uuid,
            size_blk=parent_vol.getSizeBlk() - 1,
            volFormat=sc.COW_FORMAT,
            preallocate=sc.SPARSE_VOL,
            diskType='DATA',
            volUUID=vol_uuid,
            desc="Volume with smaller size",
            srcImgUUID=parent_vol.imgUUID,
            srcVolUUID=parent_vol.volUUID)

    # Verify that snapshot can be larger than parent.

    dom.createVolume(
        imgUUID=img_uuid,
        size_blk=2 * SPARSE_VOL_SIZE // sc.BLOCK_SIZE_512,
        volFormat=sc.COW_FORMAT,
        preallocate=sc.SPARSE_VOL,
        diskType='DATA',
        volUUID=vol_uuid,
        desc="Extended volume",
        srcImgUUID=parent_vol.imgUUID,
        srcVolUUID=parent_vol.volUUID)
    vol = dom.produceVolume(img_uuid, vol_uuid)

    # Verify volume sizes obtained from metadata
    actual_parent = parent_vol.getInfo()
    assert int(actual_parent["capacity"]) == parent_vol_capacity

    actual = vol.getInfo()
    assert int(actual["capacity"]) == vol_capacity


@xfail_python3
@pytest.mark.parametrize("domain_version", [4, 5])
def test_volume_metadata_capacity_corrupted(
        tmpdir, tmp_repo, fake_access, fake_rescan, tmp_db,
        fake_task, local_fallocate, domain_version):
    # This test verifies a flow in which volume metadata capacity is corrupted.
    # This can happen e.g. a result of bug https://bugzilla.redhat.com/1700623.
    # To simulate the corrupted data, we corrupt it manually in the test.
    dom = tmp_repo.create_localfs_domain(name="domain", version=domain_version)

    img_uuid = str(uuid.uuid4())
    vol_uuid = str(uuid.uuid4())

    dom.createVolume(
        imgUUID=img_uuid,
        size_blk=2 * SPARSE_VOL_SIZE // sc.BLOCK_SIZE_512,
        volFormat=sc.COW_FORMAT,
        preallocate=sc.SPARSE_VOL,
        diskType='DATA',
        volUUID=vol_uuid,
        desc="Test volume",
        srcImgUUID=sc.BLANK_UUID,
        srcVolUUID=sc.BLANK_UUID)
    vol = dom.produceVolume(img_uuid, vol_uuid)

    # corrupt the metadata capacity manually
    md = vol.getMetadata()
    md.capacity = SPARSE_VOL_SIZE
    vol.setMetadata(md)

    # during preparation of the volume, matadata capacity should be fixed
    vol.prepare()

    actual = vol.getInfo()
    assert int(actual["capacity"]) == 2 * SPARSE_VOL_SIZE


def verify_volume_file(
        path, format, virtual_size, qemu_info, backing_file=None):
    assert qemu_info['format'] == format
    assert qemu_info['virtualsize'] == virtual_size

    vol_mode = os.stat(path).st_mode
    assert stat.S_IMODE(vol_mode) == sc.FILE_VOLUME_PERMISSIONS

    if backing_file:
        assert qemu_info['backingfile'] == backing_file
    else:
        assert 'backingfile' not in qemu_info
