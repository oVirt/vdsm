#
# Copyright 2014-2017 Red Hat, Inc.
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
import time
import uuid

import pytest

from vdsm import constants
from vdsm.storage import blockSD
from vdsm.storage import constants as sc
from vdsm.storage import exception as se
from vdsm.storage import lvm
from vdsm.storage import sd
from vdsm.storage.sdc import sdCache

from . import qemuio
from . marks import requires_root, xfail_python3
from storage.storagefakelib import fake_vg

TESTDIR = os.path.dirname(__file__)


class TestMetadataValidity:

    MIN_MD_SIZE = blockSD.VG_METADATASIZE * constants.MEGAB // 2
    MIN_MD_FREE = MIN_MD_SIZE * blockSD.VG_MDA_MIN_THRESHOLD

    def test_valid_ok(self):
        vg = fake_vg(
            vg_mda_size=self.MIN_MD_SIZE, vg_mda_free=self.MIN_MD_FREE)
        assert blockSD.metadataValidity(vg)['mdavalid']

    def test_valid_bad(self):
        vg = fake_vg(
            vg_mda_size=self.MIN_MD_SIZE - 1, vg_mda_free=self.MIN_MD_FREE)
        assert not blockSD.metadataValidity(vg)['mdavalid']

    def test_threshold_ok(self):
        vg = fake_vg(
            vg_mda_size=self.MIN_MD_SIZE, vg_mda_free=self.MIN_MD_FREE + 1)
        assert blockSD.metadataValidity(vg)['mdathreshold']

    def test_threshold_bad(self):
        vg = fake_vg(
            vg_mda_size=self.MIN_MD_SIZE, vg_mda_free=self.MIN_MD_FREE)
        assert not blockSD.metadataValidity(vg)['mdathreshold']


def fakeGetLV(vgName):
    """ This function returns lvs output in lvm.getLV() format.

    Input file name: lvs_<sdName>.out
    Input file should be the output of:
    lvs --noheadings --units b --nosuffix --separator '|' \
        -o uuid,name,vg_name,attr,size,seg_start_pe,devices,tags <sdName>

    """
    # TODO: simplify by returning fake lvs instead of parsing real lvs output.
    lvs_output = os.path.join(TESTDIR, 'lvs_%s.out' % vgName)
    lvs = []
    with open(lvs_output) as f:
        for line in f:
            fields = [field.strip() for field in line.split(lvm.SEPARATOR)]
            lvs.append(lvm.makeLV(*fields))
    return lvs


class TestGetAllVolumes:
    # TODO: add more tests, see fileSDTests.py

    def test_volumes_count(self, monkeypatch):
        monkeypatch.setattr(lvm, 'getLV', fakeGetLV)
        sdName = "3386c6f2-926f-42c4-839c-38287fac8998"
        allVols = blockSD.getAllVolumes(sdName)
        assert len(allVols) == 23

    def test_missing_tags(self, monkeypatch):
        monkeypatch.setattr(lvm, 'getLV', fakeGetLV)
        sdName = "f9e55e18-67c4-4377-8e39-5833ca422bef"
        allVols = blockSD.getAllVolumes(sdName)
        assert len(allVols) == 1


class TestDecodeValidity:

    def test_all_keys(self):
        value = ('pv:myname,uuid:Gk8q,pestart:0,'
                 'pecount:77,mapoffset:0')
        pvinfo = blockSD.decodePVInfo(value)
        assert pvinfo["guid"] == 'myname'
        assert pvinfo["uuid"] == 'Gk8q'
        assert pvinfo["pestart"] == '0'
        assert pvinfo["pecount"] == '77'
        assert pvinfo["mapoffset"] == '0'

    def test_decode_pv_colon(self):
        pvinfo = blockSD.decodePVInfo('pv:my:name')
        assert pvinfo["guid"] == 'my:name'

    @pytest.mark.xfail(reason='Comma in PV name is not supported yet')
    def test_decode_pv_comma(self):
        pvinfo = blockSD.decodePVInfo('pv:my,name')
        assert pvinfo["guid"] == 'my,name'


def test_metadata_offset(monkeypatch):
    sd_uuid = str(uuid.uuid4())
    fake_metadata = {
        sd.DMDK_VERSION: 4,
        sd.DMDK_LOGBLKSIZE: 512,
        sd.DMDK_PHYBLKSIZE: 512,
    }

    monkeypatch.setattr(sd.StorageDomainManifest, "_makeDomainLock",
                        lambda _: None)
    sd_manifest = blockSD.BlockStorageDomainManifest(sd_uuid, fake_metadata)

    assert 0 == sd_manifest.metadata_offset(0)
    assert 51200 == sd_manifest.metadata_offset(100)
    assert 0 == sd_manifest.metadata_offset(0, version=4)
    assert 51200 == sd_manifest.metadata_offset(100, version=4)
    assert 1048576 == sd_manifest.metadata_offset(0, version=5)
    assert 1867776 == sd_manifest.metadata_offset(100, version=5)


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
            blockSD.BlockStorageDomain.create(
                sc.BLANK_UUID, "test", sd.DATA_DOMAIN,
                sc.BLANK_UUID, sd.ISCSI_DOMAIN, version, block_size, alignment)


@requires_root
@xfail_python3
@pytest.mark.root
@pytest.mark.parametrize("domain_version", [3, 4, 5])
def test_create_domain_metadata(tmp_storage, tmp_repo, fake_sanlock,
                                domain_version):
    sd_uuid = str(uuid.uuid4())
    domain_name = "loop-domain"

    dev1 = tmp_storage.create_device(10 * 1024**3)
    dev2 = tmp_storage.create_device(10 * 1024**3)
    lvm.createVG(sd_uuid, [dev1, dev2], blockSD.STORAGE_UNREADY_DOMAIN_TAG,
                 128)
    vg = lvm.getVG(sd_uuid)
    pv1 = lvm.getPV(dev1)
    pv2 = lvm.getPV(dev2)

    dom = blockSD.BlockStorageDomain.create(
        sdUUID=sd_uuid,
        domainName=domain_name,
        domClass=sd.DATA_DOMAIN,
        vgUUID=vg.uuid,
        version=domain_version,
        storageType=sd.ISCSI_DOMAIN,
        block_size=sc.BLOCK_SIZE_512,
        alignment=sc.ALIGNMENT_1M)

    sdCache.knownSDs[sd_uuid] = blockSD.findDomain
    sdCache.manuallyAddDomain(dom)

    lease = sd.DEFAULT_LEASE_PARAMS
    expected = {
        # Common storage domain values.
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
        sd.DMDK_TYPE: sd.ISCSI_DOMAIN,
        sd.DMDK_VERSION: domain_version,

        # Block storge domain extra values.
        blockSD.DMDK_VGUUID: vg.uuid,

        # PV keys for blockSD.DMDK_PV_REGEX.
        "PV0": {
            'guid': os.path.basename(dev1),
            'mapoffset': '0',
            'pecount': '77',
            'pestart': '0',
            'uuid': pv1.uuid,
        },
        "PV1": {
            'guid': os.path.basename(dev2),
            'mapoffset': '77',
            'pecount': '77',
            'pestart': '0',
            'uuid': pv2.uuid,
        },
    }

    # In version 5 we removed LOGBLKSIZE and PHYBLKSIZE and added
    # ALIGNMENT and BLOCK_SIZE.
    if domain_version < 5:
        expected[sd.DMDK_LOGBLKSIZE] = sc.BLOCK_SIZE_512
        expected[sd.DMDK_PHYBLKSIZE] = sc.BLOCK_SIZE_512
    else:
        expected[sd.DMDK_ALIGNMENT] = sc.ALIGNMENT_1M
        expected[sd.DMDK_BLOCK_SIZE] = sc.BLOCK_SIZE_512

    # Tests also alignment and block size properties here.
    assert dom.alignment == sc.ALIGNMENT_1M
    assert dom.block_size == sc.BLOCK_SIZE_512

    actual = dom.getMetadata()

    assert expected == actual

    # Check that first PV is device where metadata is stored.
    assert dev1 == lvm.getVgMetadataPv(dom.sdUUID)

    lv = lvm.getLV(dom.sdUUID, sd.METADATA)
    assert int(lv.size) == blockSD.METADATA_LV_SIZE_MB * constants.MEGAB


@requires_root
@xfail_python3
@pytest.mark.root
@pytest.mark.parametrize("domain_version", [3, 4, 5])
def test_volume_life_cycle(monkeypatch, tmp_storage, tmp_repo, fake_access,
                           fake_rescan, tmp_db, fake_task, fake_sanlock,
                           domain_version):
    # as creation of block storage domain and volume is quite time consuming,
    # we test several volume operations in one test to speed up the test suite

    sd_uuid = str(uuid.uuid4())
    domain_name = "domain"

    dev = tmp_storage.create_device(20 * 1024 ** 3)
    lvm.createVG(sd_uuid, [dev], blockSD.STORAGE_UNREADY_DOMAIN_TAG, 128)
    vg = lvm.getVG(sd_uuid)

    dom = blockSD.BlockStorageDomain.create(
        sdUUID=sd_uuid,
        domainName=domain_name,
        domClass=sd.DATA_DOMAIN,
        vgUUID=vg.uuid,
        version=domain_version,
        storageType=sd.ISCSI_DOMAIN,
        block_size=sc.BLOCK_SIZE_512,
        alignment=sc.ALIGNMENT_1M)

    sdCache.knownSDs[sd_uuid] = blockSD.findDomain
    sdCache.manuallyAddDomain(dom)

    img_uuid = str(uuid.uuid4())
    vol_uuid = str(uuid.uuid4())
    vol_capacity = 10 * 1024**3
    vol_size = vol_capacity // sc.BLOCK_SIZE_512
    vol_desc = "Test volume"

    # Create domain directory structure.
    dom.refresh()
    # Attache repo pool - SD expects at least one pool is attached.
    dom.attach(tmp_repo.pool_id)

    with monkeypatch.context() as mc:
        mc.setattr(time, "time", lambda: 1550522547)
        dom.createVolume(
            imgUUID=img_uuid,
            size=vol_size,
            volFormat=sc.COW_FORMAT,
            preallocate=sc.SPARSE_VOL,
            diskType=sc.DATA_DISKTYPE,
            volUUID=vol_uuid,
            desc=vol_desc,
            srcImgUUID=sc.BLANK_UUID,
            srcVolUUID=sc.BLANK_UUID)

    # test create volume
    vol = dom.produceVolume(img_uuid, vol_uuid)

    # Get the metadata slot, used for volume metadata and volume lease offset.
    _, slot = vol.getMetadataId()

    lease = dom.getVolumeLease(img_uuid, vol_uuid)

    assert lease.name == vol.volUUID
    assert lease.path == "/dev/{}/leases".format(sd_uuid)
    assert lease.offset == (blockSD.RESERVED_LEASES + slot) * dom.alignment

    # Test that we created a sanlock resource for this volume.
    resource = fake_sanlock.read_resource(
        lease.path,
        lease.offset,
        align=dom.alignment,
        sector=dom.block_size)

    assert resource == {
        "acquired": False,
        "align": dom.alignment,
        "lockspace": vol.sdUUID,
        "resource": vol.volUUID,
        "sector": dom.block_size,
        "version": 0,
    }

    # Test volume info.
    actual = vol.getInfo()

    assert int(actual["capacity"]) == vol_capacity
    assert int(actual["ctime"]) == 1550522547
    assert actual["description"] == vol_desc
    assert actual["disktype"] == "DATA"
    assert actual["domain"] == sd_uuid
    assert actual["format"] == "COW"
    assert actual["lease"] == {
        "offset": lease.offset,
        "owners": [],
        "path": lease.path,
        "version": None,
    }
    assert actual["parent"] == sc.BLANK_UUID
    assert actual["status"] == "OK"
    assert actual["type"] == "SPARSE"
    assert actual["voltype"] == "LEAF"
    assert actual["uuid"] == vol_uuid

    vol_path = vol.getVolumePath()

    # test volume prepare
    assert os.path.islink(vol_path)
    assert not os.path.exists(vol_path)

    vol.prepare()

    assert os.path.exists(vol_path)

    # verify we can really write and read to an image
    qemuio.write_pattern(vol_path, "qcow2")
    qemuio.verify_pattern(vol_path, "qcow2")

    # test volume teardown
    vol.teardown(sd_uuid, vol_uuid)

    assert os.path.islink(vol_path)
    assert not os.path.exists(vol_path)

    # test also deleting of the volume
    vol.delete(postZero=False, force=False, discard=False)

    # verify lvm with volume is deleted
    assert not os.path.islink(vol.getVolumePath())
    with pytest.raises(se.LogicalVolumeDoesNotExistError):
        lvm.getLV(sd_uuid, vol_uuid)

    # verify also metadata from metadata lv is deleted
    data = dom.manifest.read_metadata_block(slot)
    assert data == b"\0" * sc.METADATA_SIZE


@requires_root
@xfail_python3
@pytest.mark.root
def test_volume_metadata(tmp_storage, tmp_repo, fake_access, fake_rescan,
                         tmp_db, fake_task, fake_sanlock):
    sd_uuid = str(uuid.uuid4())

    dev = tmp_storage.create_device(20 * 1024 ** 3)
    lvm.createVG(sd_uuid, [dev], blockSD.STORAGE_UNREADY_DOMAIN_TAG, 128)
    vg = lvm.getVG(sd_uuid)

    dom = blockSD.BlockStorageDomain.create(
        sdUUID=sd_uuid,
        domainName="domain",
        domClass=sd.DATA_DOMAIN,
        vgUUID=vg.uuid,
        version=4,
        storageType=sd.ISCSI_DOMAIN,
        block_size=sc.BLOCK_SIZE_512,
        alignment=sc.ALIGNMENT_1M)

    sdCache.knownSDs[sd_uuid] = blockSD.findDomain
    sdCache.manuallyAddDomain(dom)

    dom.refresh()
    dom.attach(tmp_repo.pool_id)

    img_uuid = str(uuid.uuid4())
    vol_uuid = str(uuid.uuid4())

    dom.createVolume(
        desc="old description",
        diskType="DATA",
        imgUUID=img_uuid,
        preallocate=sc.SPARSE_VOL,
        size=10 * 1024**3,
        srcImgUUID=sc.BLANK_UUID,
        srcVolUUID=sc.BLANK_UUID,
        volFormat=sc.COW_FORMAT,
        volUUID=vol_uuid)

    vol = dom.produceVolume(img_uuid, vol_uuid)

    # Test metadata offset
    _, slot = vol.getMetadataId()
    offset = dom.manifest.metadata_offset(slot)
    assert offset == slot * blockSD.METADATA_SLOT_SIZE_V4

    meta_path = dom.manifest.metadata_volume_path()

    # Change metadata.
    md = vol.getMetadata()
    md.description = "new description"
    vol.setMetadata(md)
    with open(meta_path) as f:
        f.seek(offset)
        data = f.read(sc.METADATA_SIZE)
    data = data.rstrip("\0")
    assert data == md.storage_format(4)

    # Add additioanl metadata.
    md = vol.getMetadata()
    vol.setMetadata(md, CAP=md.capacity)
    with open(meta_path) as f:
        f.seek(offset)
        data = f.read(sc.METADATA_SIZE)
    data = data.rstrip("\0")
    assert data == md.storage_format(4, CAP=md.capacity)


@requires_root
@xfail_python3
@pytest.mark.root
@pytest.mark.parametrize("domain_version", [4, 5])
def test_create_snapshot_size(
        tmp_storage, tmp_repo, fake_access, fake_rescan, tmp_db, fake_task,
        fake_sanlock, domain_version):
    # This test was added to verify fix for https://bugzilla.redhat.com/1700623
    # As a result of this bug, there can be volumes with corrupted metadata
    # capacity. The metadata of such volume should be fixed when the volume is
    # prepared. As the creation of tmp storage for block SD is time consuming,
    # let's test this flow also in this test.
    sd_uuid = str(uuid.uuid4())

    dev = tmp_storage.create_device(20 * 1024 ** 3)
    lvm.createVG(sd_uuid, [dev], blockSD.STORAGE_UNREADY_DOMAIN_TAG, 128)
    vg = lvm.getVG(sd_uuid)

    dom = blockSD.BlockStorageDomain.create(
        sdUUID=sd_uuid,
        domainName="domain",
        domClass=sd.DATA_DOMAIN,
        vgUUID=vg.uuid,
        version=domain_version,
        storageType=sd.ISCSI_DOMAIN,
        block_size=sc.BLOCK_SIZE_512,
        alignment=sc.ALIGNMENT_1M)

    sdCache.knownSDs[sd_uuid] = blockSD.findDomain
    sdCache.manuallyAddDomain(dom)

    dom.refresh()
    dom.attach(tmp_repo.pool_id)

    img_uuid = str(uuid.uuid4())
    parent_vol_uuid = str(uuid.uuid4())
    parent_vol_capacity = constants.GIB
    vol_uuid = str(uuid.uuid4())
    vol_capacity = 2 * parent_vol_capacity

    # Create parent volume.

    dom.createVolume(
        imgUUID=img_uuid,
        size=parent_vol_capacity // sc.BLOCK_SIZE_512,
        volFormat=sc.RAW_FORMAT,
        preallocate=sc.PREALLOCATED_VOL,
        diskType='DATA',
        volUUID=parent_vol_uuid,
        desc="Test parent volume",
        srcImgUUID=sc.BLANK_UUID,
        srcVolUUID=sc.BLANK_UUID,
        initialSize=None)

    parent_vol = dom.produceVolume(img_uuid, parent_vol_uuid)

    # Verify that snapshot cannot be smaller than parent.

    with pytest.raises(se.InvalidParameterException):
        dom.createVolume(
            imgUUID=img_uuid,
            size=parent_vol.getSize() - 1,
            volFormat=sc.COW_FORMAT,
            preallocate=sc.SPARSE_VOL,
            diskType='DATA',
            volUUID=vol_uuid,
            desc="Extended volume",
            srcImgUUID=parent_vol.imgUUID,
            srcVolUUID=parent_vol.volUUID,
            initialSize=None)

    # Verify that snapshot can be bigger than parent.

    dom.createVolume(
        imgUUID=img_uuid,
        size=vol_capacity // sc.BLOCK_SIZE_512,
        volFormat=sc.COW_FORMAT,
        preallocate=sc.SPARSE_VOL,
        diskType='DATA',
        volUUID=vol_uuid,
        desc="Extended volume",
        srcImgUUID=parent_vol.imgUUID,
        srcVolUUID=parent_vol.volUUID,
        initialSize=None)

    vol = dom.produceVolume(img_uuid, vol_uuid)

    # Verify volume sizes obtained from metadata
    actual_parent = parent_vol.getInfo()
    assert int(actual_parent["capacity"]) == parent_vol_capacity

    actual = vol.getInfo()
    assert int(actual["capacity"]) == vol_capacity

    # Now test the flow in which metadata capacity is corrupted.
    # Corrupt the metadata capacity manually.
    md = vol.getMetadata()
    md.capacity = vol_capacity // 2
    vol.setMetadata(md)

    # During preparation of the volume, matadata capacity should be fixed.
    vol.prepare()

    actual = vol.getInfo()
    assert int(actual["capacity"]) == vol_capacity
