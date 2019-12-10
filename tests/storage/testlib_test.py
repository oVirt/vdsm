# Copyright 2016-2017 Red Hat, Inc.
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

from testlib import make_uuid
from testlib import TEMPDIR

import pytest

from storage.storagetestlib import (
    Aborting,
    FakeGuardedLock,
    fake_block_env,
    fake_env,
    fake_file_env,
    make_block_volume,
    make_file_volume,
    make_qemu_chain,
    verify_qemu_chain,
    write_qemu_chain,
)

from . import qemuio

from vdsm import utils
from vdsm.common.units import MiB
from vdsm.storage import blockSD
from vdsm.storage import constants as sc
from vdsm.storage import fileSD
from vdsm.storage import fileVolume
from vdsm.storage import sd


# Test fake file environment

def test_no_fakelvm():
    with fake_file_env() as env:
        assert not hasattr(env, 'lvm')


def test_repo_location():
    with fake_file_env() as env:
        # Verify that the environment uses expected tmp dir.
        assert env.tmpdir.startswith(TEMPDIR)

        # Verify that global REPO consntats are patched.
        assert sc.REPO_DATA_CENTER == env.tmpdir
        repo_mnt_dir = os.path.join(sc.REPO_DATA_CENTER, "mnt")
        assert sc.REPO_MOUNT_DIR == repo_mnt_dir

        # And domain is mounted in the patched environment.
        dom = env.sd_manifest
        mountpoint = os.path.join(sc.REPO_MOUNT_DIR, "server:_path")
        assert dom.mountpoint == mountpoint


def test_domain_structure_file_env():
    with fake_file_env() as env:
        assert os.path.exists(env.sd_manifest.metafile)
        images_dir = os.path.dirname(env.sd_manifest.getImagePath('foo'))
        assert os.path.exists(images_dir)


def test_domain_metadata_io_file_env():
    with fake_file_env() as env:
        desc = 'foo'
        set_domain_metaparams(env.sd_manifest, {sd.DMDK_DESCRIPTION: desc})

        # Test that metadata is persisted to our temporary storage area.
        domain_dir = env.sd_manifest.domaindir
        manifest = fileSD.FileStorageDomainManifest(domain_dir)
        assert desc == manifest.getMetaParam(sd.DMDK_DESCRIPTION)


@pytest.mark.parametrize("env_type", ["file", "block"])
def test_default_domain_version(env_type):
    with fake_env(env_type) as env:
        assert 3 == env.sd_manifest.getVersion()


@pytest.mark.parametrize("env_type, sd_version", [
    ("file", 3),
    ("file", 4),
    ("block", 3),
    ("block", 4),
])
def test_domain_version(env_type, sd_version):
    with fake_env(env_type, sd_version=sd_version) as env:
        assert sd_version == env.sd_manifest.getVersion()


def test_volume_structure():
    with fake_file_env() as env:
        img_id = make_uuid()
        vol_id = make_uuid()
        make_file_volume(env.sd_manifest, 0, img_id, vol_id)
        image_dir = env.sd_manifest.getImagePath(img_id)
        files = (vol_id,
                 vol_id + sc.LEASE_FILEEXT,
                 vol_id + fileVolume.META_FILEEXT)
        for f in files:
            path = os.path.join(image_dir, f)
            assert os.path.exists(path)


@pytest.mark.parametrize("vol_type", [sc.LEAF_VOL, sc.INTERNAL_VOL])
def test_volume_type_file_env(vol_type):
    with fake_file_env() as env:
        img_id = make_uuid()
        vol_id = make_uuid()
        make_file_volume(env.sd_manifest, 0, img_id, vol_id, vol_type=vol_type)
        vol = env.sd_manifest.produceVolume(img_id, vol_id)
        assert vol.getVolType() == sc.type2name(vol_type)


def test_volume_metadata_io_file_env():
    with fake_file_env() as env:
        size = 1 * MiB
        img_id = make_uuid()
        vol_id = make_uuid()
        make_file_volume(env.sd_manifest, size, img_id, vol_id)
        vol = env.sd_manifest.produceVolume(img_id, vol_id)
        desc = 'foo'
        vol.setDescription(desc)

        # Test that metadata is persisted to our temporary storage area.
        vol = env.sd_manifest.produceVolume(img_id, vol_id)
        assert desc == vol.getDescription()


# Test fake block environment

def test_repopath_location():
    with fake_block_env() as env:
        assert env.sd_manifest.getRepoPath().startswith(TEMPDIR)


def test_domain_structure_block_env():
    with fake_block_env() as env:
        vg_name = env.sd_manifest.sdUUID
        md_path = env.lvm.lvPath(vg_name, sd.METADATA)
        assert os.path.exists(md_path)

        version = env.sd_manifest.getVersion()
        for lv in env.sd_manifest.special_volumes(version):
            assert lv == env.lvm.getLV(vg_name, lv).name

        images_dir = os.path.join(env.sd_manifest.domaindir, sd.DOMAIN_IMAGES)
        assert os.path.exists(images_dir)

        # Check the storage repository.
        repo_path = env.sd_manifest.getRepoPath()
        domain_link = os.path.join(repo_path, env.sd_manifest.sdUUID)
        assert os.path.islink(domain_link)
        assert env.sd_manifest.domaindir == os.readlink(domain_link)


def test_domain_metadata_io_block_env():
    with fake_block_env() as env:
        desc = 'foo'
        set_domain_metaparams(env.sd_manifest, {sd.DMDK_DESCRIPTION: desc})

        # Test that metadata is persisted to our temporary storage area.
        sd_id = env.sd_manifest.sdUUID
        manifest = blockSD.BlockStorageDomainManifest(sd_id)
        assert desc == manifest.getMetaParam(sd.DMDK_DESCRIPTION)


@pytest.mark.parametrize("vol_type", [sc.LEAF_VOL, sc.INTERNAL_VOL])
def test_volume_type_block_env(vol_type):
    with fake_block_env() as env:
        img_id = make_uuid()
        vol_id = make_uuid()
        make_block_volume(
            env.lvm, env.sd_manifest, 0, img_id, vol_id, vol_type=vol_type)
        vol = env.sd_manifest.produceVolume(img_id, vol_id)
        assert vol.getVolType() == sc.type2name(vol_type)


@pytest.mark.parametrize("size_param", [
    MiB,
    2 * MiB - 1,
    1,
    (sc.VG_EXTENT_SIZE_MB - 1) * MiB,
    sc.VG_EXTENT_SIZE_MB * MiB + 1,
])
def test_volume_size_alignment(size_param):
    with fake_block_env() as env:
        sd_id = env.sd_manifest.sdUUID
        img_id = make_uuid()
        vol_id = make_uuid()
        make_block_volume(env.lvm, env.sd_manifest, size_param, img_id, vol_id)
        vol = env.sd_manifest.produceVolume(img_id, vol_id)

        extent_size = sc.VG_EXTENT_SIZE_MB * MiB
        expected_size = utils.round(size_param, extent_size)
        assert expected_size == vol.getCapacity()
        assert expected_size == int(env.lvm.getLV(sd_id, vol_id).size)
        lv_file_size = os.stat(env.lvm.lvPath(sd_id, vol_id)).st_size
        assert expected_size == lv_file_size


def test_volume_metadata_io_block_env():
    with fake_block_env() as env:
        sd_id = env.sd_manifest.sdUUID
        img_id = make_uuid()
        vol_id = make_uuid()
        size_mb = sc.VG_EXTENT_SIZE_MB
        size = size_mb * MiB
        make_block_volume(env.lvm, env.sd_manifest, size, img_id, vol_id)

        assert vol_id == env.lvm.getLV(sd_id, vol_id).name
        vol = env.sd_manifest.produceVolume(img_id, vol_id)
        assert size == vol.getCapacity()
        desc = 'foo'
        vol.setDescription(desc)

        # Test that metadata is persisted to our temporary storage area.
        vol = env.sd_manifest.produceVolume(img_id, vol_id)
        assert desc == vol.getDescription()


def test_volume_accessibility():
    with fake_block_env() as env:
        sd_id = env.sd_manifest.sdUUID
        img_id = make_uuid()
        vol_id = make_uuid()
        make_block_volume(env.lvm, env.sd_manifest, 1 * MiB, img_id, vol_id)

        assert os.path.isfile(env.lvm.lvPath(sd_id, vol_id))

        domain_path = os.path.join(env.sd_manifest.domaindir,
                                   sd.DOMAIN_IMAGES,
                                   img_id,
                                   vol_id)
        repo_path = os.path.join(env.sd_manifest.getRepoPath(),
                                 sd_id,
                                 sd.DOMAIN_IMAGES,
                                 img_id,
                                 vol_id)
        assert repo_path != domain_path
        # The links to the dev are created only when producing the volume.
        assert not os.path.isfile(domain_path)
        assert not os.path.isfile(repo_path)

        env.sd_manifest.produceVolume(img_id, vol_id)
        assert os.path.samefile(repo_path, domain_path)


# Test chain verification

@pytest.mark.parametrize("storage_type", ["file", "block"])
def test_make_qemu_chain(storage_type):
    with fake_env(storage_type) as env:
        vol_list = make_qemu_chain(env, 0, sc.RAW_FORMAT, 2)
        msg = "Internal volume has wrong type: %s" % vol_list[0].getVolType()
        assert vol_list[0].isInternal(), msg
        msg = "Leaf volume has wrong type: %s" % vol_list[1].getVolType()
        assert vol_list[1].isLeaf(), msg


# Although these tests use file and block environments, due to the
# underlying implementation, all reads and writes are to regular files.
@pytest.mark.parametrize("storage_type", ["file", "block"])
def test_verify_chain(storage_type):
    with fake_env(storage_type) as env:
        vol_list = make_qemu_chain(env, MiB, sc.RAW_FORMAT, 2)
        write_qemu_chain(vol_list)
        verify_qemu_chain(vol_list)


@pytest.mark.parametrize("storage_type", ["file", "block"])
def test_reversed_chain_raises(storage_type):
    with fake_env(storage_type) as env:
        vol_list = make_qemu_chain(env, MiB, sc.RAW_FORMAT, 2)
        write_qemu_chain(reversed(vol_list))
        with pytest.raises(qemuio.VerificationError):
            verify_qemu_chain(vol_list)


@pytest.mark.parametrize("storage_type", ["file", "block"])
def test_pattern_written_to_base_raises(storage_type):
    with fake_env(storage_type) as env:
        vol_list = make_qemu_chain(env, MiB, sc.RAW_FORMAT, 3)

        # Writes the entire pattern into the base volume.
        bad_list = vol_list[:1] * 3
        write_qemu_chain(bad_list)
        with pytest.raises(qemuio.VerificationError):
            verify_qemu_chain(vol_list)


def set_domain_metaparams(manifest, params):
    # XXX: Replace calls to this function with the proper manifest APIs once
    # the set* methods are moved from StorageDomain to StorageDomainManifest.
    manifest._metadata.update(params)


class OtherFakeLock(FakeGuardedLock):
    pass


# Test FakeGuardedLock

def test_properties():
    a = FakeGuardedLock('ns', 'name', 'mode', [])
    assert 'ns' == a.ns
    assert 'name' == a.name
    assert 'mode' == a.mode


def test_different_types_not_equal():
    a = FakeGuardedLock('ns', 'name', 'mode', [])
    b = OtherFakeLock('ns', 'name', 'mode', [])
    assert not a.__eq__(b)
    assert a.__ne__(b)


def test_different_types_sortable():
    a = FakeGuardedLock('nsA', 'name', 'mode', [])
    b = OtherFakeLock('nsB', 'name', 'mode', [])
    assert a < b
    assert not b < a
    assert [a, b] == sorted([b, a])


@pytest.mark.parametrize("a, b", [
    (('nsA', 'nameA', 'mode'), ('nsB', 'nameA', 'mode')),
    (('nsA', 'nameA', 'mode'), ('nsA', 'nameB', 'mode')),
])
def test_less_than(a, b):
    ns_a, name_a, mode_a = a
    ns_b, name_b, mode_b = b
    b = FakeGuardedLock(ns_b, name_b, mode_b, [])
    a = FakeGuardedLock(ns_a, name_a, mode_a, [])
    assert a < b


def test_equality():
    a = FakeGuardedLock('ns', 'name', 'mode', [])
    b = FakeGuardedLock('ns', 'name', 'mode', [])
    assert a == b


def test_mode_used_for_equality():
    a = FakeGuardedLock('nsA', 'nameA', 'modeA', [])
    b = FakeGuardedLock('nsA', 'nameA', 'modeB', [])
    assert a != b


def test_mode_ignored_for_sorting():
    a = FakeGuardedLock('nsA', 'nameA', 'modeA', [])
    b = FakeGuardedLock('nsA', 'nameA', 'modeB', [])
    assert not a < b
    assert not b < a


def test_acquire_and_release():
    log = []
    expected = [('acquire', 'ns', 'name', 'mode'),
                ('release', 'ns', 'name', 'mode')]
    lock = FakeGuardedLock('ns', 'name', 'mode', log)
    lock.acquire()
    assert expected[:1] == log
    lock.release()
    assert expected == log


# Test Aborting

def test_aborting_flow():
    aborting = Aborting(5)
    for i in range(5):
        assert aborting() is False
    assert aborting() is True
