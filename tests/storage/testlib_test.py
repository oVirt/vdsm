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

import os

from testlib import expandPermutations, permutations
from testlib import make_uuid
from testlib import namedTemporaryDir
from testlib import VdsmTestCase
from testlib import TEMPDIR

from storage.storagetestlib import (
    Aborting,
    ChainVerificationError,
    FakeGuardedLock,
    fake_block_env,
    fake_env,
    fake_file_env,
    make_block_volume,
    make_file_volume,
    make_qemu_chain,
    qemu_pattern_verify,
    qemu_pattern_write,
    verify_qemu_chain,
    write_qemu_chain,
)

from vdsm import cmdutils
from vdsm import utils
from vdsm.storage import blockSD
from vdsm.storage import constants as sc
from vdsm.storage import fileSD
from vdsm.storage import fileVolume
from vdsm.storage import qemuimg
from vdsm.storage import sd

MB = 1024 ** 2


@expandPermutations
class TestFakeFileEnv(VdsmTestCase):

    def test_no_fakelvm(self):
        with fake_file_env() as env:
            self.assertFalse(hasattr(env, 'lvm'))

    def test_repopath_location(self):
        with fake_file_env() as env:
            self.assertTrue(env.sd_manifest.getRepoPath().startswith(TEMPDIR))

    def test_domain_structure(self):
        with fake_file_env() as env:
            self.assertTrue(os.path.exists(env.sd_manifest.metafile))
            images_dir = os.path.dirname(env.sd_manifest.getImagePath('foo'))
            self.assertTrue(os.path.exists(images_dir))

    def test_domain_metadata_io(self):
        with fake_file_env() as env:
            desc = 'foo'
            set_domain_metaparams(env.sd_manifest, {sd.DMDK_DESCRIPTION: desc})

            # Test that metadata is persisted to our temporary storage area
            domain_dir = env.sd_manifest.domaindir
            manifest = fileSD.FileStorageDomainManifest(domain_dir)
            self.assertEqual(desc, manifest.getMetaParam(sd.DMDK_DESCRIPTION))

    @permutations((("file",), ("block",),))
    def test_default_domain_version(self, env_type):
        with fake_env(env_type) as env:
            self.assertEqual(3, env.sd_manifest.getVersion())

    @permutations((
        # env_type, sd_version
        ("file", 3),
        ("file", 4),
        ("block", 3),
        ("block", 4),
    ))
    def test_domain_version(self, env_type, sd_version):
        with fake_env(env_type, sd_version=sd_version) as env:
            self.assertEqual(sd_version, env.sd_manifest.getVersion())

    def test_volume_structure(self):
        with fake_file_env() as env:
            img_id = make_uuid()
            vol_id = make_uuid()
            make_file_volume(env.sd_manifest, 0, img_id, vol_id)
            image_dir = env.sd_manifest.getImagePath(img_id)
            files = (vol_id, vol_id + sc.LEASE_FILEEXT,
                     vol_id + fileVolume.META_FILEEXT)
            for f in files:
                path = os.path.join(image_dir, f)
                self.assertTrue(os.path.exists(path))

    @permutations((
        # vol_type
        (sc.LEAF_VOL, ),
        (sc.INTERNAL_VOL, ),
    ))
    def test_volume_type(self, vol_type):
        with fake_file_env() as env:
            img_id = make_uuid()
            vol_id = make_uuid()
            make_file_volume(env.sd_manifest, 0, img_id, vol_id,
                             vol_type=vol_type)
            vol = env.sd_manifest.produceVolume(img_id, vol_id)
            self.assertEqual(vol.getVolType(), sc.type2name(vol_type))

    def test_volume_metadata_io(self):
        with fake_file_env() as env:
            size = 1 * MB
            img_id = make_uuid()
            vol_id = make_uuid()
            make_file_volume(env.sd_manifest, size, img_id, vol_id)
            vol = env.sd_manifest.produceVolume(img_id, vol_id)
            desc = 'foo'
            vol.setDescription(desc)

            # Test that metadata is persisted to our temporary storage area
            vol = env.sd_manifest.produceVolume(img_id, vol_id)
            self.assertEqual(desc, vol.getDescription())


@expandPermutations
class TestFakeBlockEnv(VdsmTestCase):

    def test_repopath_location(self):
        with fake_block_env() as env:
            self.assertTrue(env.sd_manifest.getRepoPath().startswith(TEMPDIR))

    def test_domain_structure(self):
        with fake_block_env() as env:
            vg_name = env.sd_manifest.sdUUID
            md_path = env.lvm.lvPath(vg_name, sd.METADATA)
            self.assertTrue(os.path.exists(md_path))

            version = env.sd_manifest.getVersion()
            for lv in env.sd_manifest.special_volumes(version):
                self.assertEqual(lv, env.lvm.getLV(vg_name, lv).name)

            images_dir = os.path.join(env.sd_manifest.domaindir,
                                      sd.DOMAIN_IMAGES)
            self.assertTrue(os.path.exists(images_dir))

            # Check the storage repository
            repo_path = env.sd_manifest.getRepoPath()
            domain_link = os.path.join(repo_path, env.sd_manifest.sdUUID)
            self.assertTrue(os.path.islink(domain_link))
            self.assertEqual(env.sd_manifest.domaindir,
                             os.readlink(domain_link))

    def test_domain_metadata_io(self):
        with fake_block_env() as env:
            desc = 'foo'
            set_domain_metaparams(env.sd_manifest, {sd.DMDK_DESCRIPTION: desc})

            # Test that metadata is persisted to our temporary storage area
            sd_id = env.sd_manifest.sdUUID
            manifest = blockSD.BlockStorageDomainManifest(sd_id)
            self.assertEqual(desc, manifest.getMetaParam(sd.DMDK_DESCRIPTION))

    @permutations((
        # vol_type
        (sc.LEAF_VOL, ),
        (sc.INTERNAL_VOL, ),
    ))
    def test_volume_type(self, vol_type):
        with fake_block_env() as env:
            img_id = make_uuid()
            vol_id = make_uuid()
            make_block_volume(env.lvm, env.sd_manifest, 0,
                              img_id, vol_id, vol_type=vol_type)
            vol = env.sd_manifest.produceVolume(img_id, vol_id)
            self.assertEqual(vol.getVolType(), sc.type2name(vol_type))

    @permutations((
        (MB,),
        (2 * MB - 1,),
        (1,),
        ((sc.VG_EXTENT_SIZE_MB - 1) * MB,),
        (sc.VG_EXTENT_SIZE_MB * MB + 1,),
    ))
    def test_volume_size_alignment(self, size_param):
        with fake_block_env() as env:
            sd_id = env.sd_manifest.sdUUID
            img_id = make_uuid()
            vol_id = make_uuid()
            make_block_volume(env.lvm, env.sd_manifest, size_param,
                              img_id, vol_id)
            vol = env.sd_manifest.produceVolume(img_id, vol_id)

            extent_size = sc.VG_EXTENT_SIZE_MB * MB
            expected_size = utils.round(size_param, extent_size)
            self.assertEqual(expected_size / sc.BLOCK_SIZE, vol.getSize())
            self.assertEqual(expected_size,
                             int(env.lvm.getLV(sd_id, vol_id).size))
            lv_file_size = os.stat(env.lvm.lvPath(sd_id, vol_id)).st_size
            self.assertEqual(expected_size, lv_file_size)

    def test_volume_metadata_io(self):
        with fake_block_env() as env:
            sd_id = env.sd_manifest.sdUUID
            img_id = make_uuid()
            vol_id = make_uuid()
            size_mb = sc.VG_EXTENT_SIZE_MB
            size = size_mb * MB
            size_blk = size_mb * MB / sc.BLOCK_SIZE
            make_block_volume(env.lvm, env.sd_manifest, size,
                              img_id, vol_id)

            self.assertEqual(vol_id, env.lvm.getLV(sd_id, vol_id).name)
            vol = env.sd_manifest.produceVolume(img_id, vol_id)
            self.assertEqual(size_blk, vol.getSize())
            desc = 'foo'
            vol.setDescription(desc)

            # Test that metadata is persisted to our temporary storage area
            vol = env.sd_manifest.produceVolume(img_id, vol_id)
            self.assertEqual(desc, vol.getDescription())

    def test_volume_accessibility(self):
        with fake_block_env() as env:
            sd_id = env.sd_manifest.sdUUID
            img_id = make_uuid()
            vol_id = make_uuid()
            make_block_volume(env.lvm, env.sd_manifest, 1 * MB, img_id, vol_id)

            self.assertTrue(os.path.isfile(env.lvm.lvPath(sd_id, vol_id)))

            domain_path = os.path.join(env.sd_manifest.domaindir,
                                       sd.DOMAIN_IMAGES,
                                       img_id,
                                       vol_id)
            repo_path = os.path.join(env.sd_manifest.getRepoPath(),
                                     sd_id,
                                     sd.DOMAIN_IMAGES,
                                     img_id,
                                     vol_id)
            self.assertNotEqual(repo_path, domain_path)
            # The links to the dev are created only when producing the volume
            self.assertFalse(os.path.isfile(domain_path))
            self.assertFalse(os.path.isfile(repo_path))

            env.sd_manifest.produceVolume(img_id, vol_id)
            self.assertTrue(os.path.samefile(repo_path, domain_path))


@expandPermutations
class TestQemuPatternVerification(VdsmTestCase):

    @permutations(((qemuimg.FORMAT.QCOW2,), (qemuimg.FORMAT.RAW,)))
    def test_match(self, img_format):
        with namedTemporaryDir() as tmpdir:
            path = os.path.join(tmpdir, 'test')
            qemuimg.create(path, '1m', img_format)
            qemu_pattern_write(path, img_format)
            qemu_pattern_verify(path, img_format)

    @permutations((
        (0, 128),
        (10 * 1024, 5 * 1024)
    ))
    def test_match_custom_offset_and_len(self, offset, len):
        with namedTemporaryDir() as tmpdir:
            path = os.path.join(tmpdir, 'test')
            qemuimg.create(path, '1m', qemuimg.FORMAT.QCOW2)
            qemu_pattern_write(path, qemuimg.FORMAT.QCOW2,
                               offset=offset, len=len)
            qemu_pattern_verify(path, qemuimg.FORMAT.QCOW2, offset=offset,
                                len=len)

    @permutations(((qemuimg.FORMAT.QCOW2,), (qemuimg.FORMAT.RAW,)))
    def test_no_match(self, img_format):
        with namedTemporaryDir() as tmpdir:
            path = os.path.join(tmpdir, 'test')
            qemuimg.create(path, '1m', img_format)
            qemu_pattern_write(path, img_format, pattern=2)
            self.assertRaises(ChainVerificationError,
                              qemu_pattern_verify, path, img_format, pattern=4)

    @permutations((
        # storage_type
        ('file', ),
        ('block', )
    ))
    def test_make_qemu_chain(self, storage_type):
        with fake_env(storage_type) as env:
            vol_list = make_qemu_chain(env, 0, sc.RAW_FORMAT, 2)
            self.assertTrue(vol_list[0].isInternal(),
                            "Internal volume has wrong type: %s"
                            % vol_list[0].getVolType())
            self.assertTrue(vol_list[1].isLeaf(),
                            "Leaf volume has wrong type: %s"
                            % vol_list[1].getVolType())

    # Although these tests use file and block environments, due to the
    # underlying implementation, all reads and writes are to regular files.
    @permutations((('file',), ('block',)))
    def test_verify_chain(self, storage_type):
        with fake_env(storage_type) as env:
            vol_list = make_qemu_chain(env, MB, sc.RAW_FORMAT, 2)
            write_qemu_chain(vol_list)
            verify_qemu_chain(vol_list)

    @permutations((('file',), ('block',)))
    def test_reversed_chain_raises(self, storage_type):
        with fake_env(storage_type) as env:
            vol_list = make_qemu_chain(env, MB, sc.RAW_FORMAT, 2)
            write_qemu_chain(reversed(vol_list))
            self.assertRaises(ChainVerificationError,
                              verify_qemu_chain, vol_list)

    @permutations((('file',), ('block',)))
    def test_pattern_written_to_base_raises(self, storage_type):
        with fake_env(storage_type) as env:
            vol_list = make_qemu_chain(env, MB, sc.RAW_FORMAT, 3)

            # Writes the entire pattern into the base volume
            bad_list = vol_list[:1] * 3
            write_qemu_chain(bad_list)
            self.assertRaises(ChainVerificationError,
                              verify_qemu_chain, vol_list)

    @permutations([(qemuimg.FORMAT.QCOW2,), (qemuimg.FORMAT.RAW,)])
    def test_read_missing_file_raises(self, format):
        with self.assertRaises(cmdutils.Error):
            qemu_pattern_verify("/no/such/file", format)

    def test_read_wrong_format_raises(self):
        with namedTemporaryDir() as tmpdir:
            path = os.path.join(tmpdir, "test.qcow2")
            qemuimg.create(path, "1m", qemuimg.FORMAT.RAW)
            with self.assertRaises(cmdutils.Error):
                qemu_pattern_verify(path, qemuimg.FORMAT.QCOW2)

    def test_read_bad_chain_raises(self):
        with namedTemporaryDir() as tmpdir:
            # Create a good chain.
            base_qcow2 = os.path.join(tmpdir, "base.qcow2")
            qemuimg.create(base_qcow2, "1m", qemuimg.FORMAT.QCOW2)
            top = os.path.join(tmpdir, "top.qcow2")
            qemuimg.create(top, "1m", qemuimg.FORMAT.QCOW2, backing=base_qcow2,
                           backingFormat=qemuimg.FORMAT.QCOW2)

            # Create a broken chain using unsafe rebase with the wrong backing
            # format.
            base_raw = os.path.join(tmpdir, "base.raw")
            qemuimg.create(base_raw, "1m", qemuimg.FORMAT.RAW)
            operation = qemuimg.rebase(top,
                                       backing=base_raw,
                                       format=qemuimg.FORMAT.QCOW2,
                                       backingFormat=qemuimg.FORMAT.QCOW2,
                                       unsafe=True)
            operation.run()
            with self.assertRaises(cmdutils.Error):
                qemu_pattern_verify(top, qemuimg.FORMAT.QCOW2)


def set_domain_metaparams(manifest, params):
    # XXX: Replace calls to this function with the proper manifest APIs once
    # the set* methods are moved from StorageDomain to StorageDomainManifest.
    manifest._metadata.update(params)


class OtherFakeLock(FakeGuardedLock):
    pass


@expandPermutations
class TestFakeGuardedLock(VdsmTestCase):

    def test_properties(self):
        a = FakeGuardedLock('ns', 'name', 'mode', [])
        self.assertEqual('ns', a.ns)
        self.assertEqual('name', a.name)
        self.assertEqual('mode', a.mode)

    def test_different_types_not_equal(self):
        a = FakeGuardedLock('ns', 'name', 'mode', [])
        b = OtherFakeLock('ns', 'name', 'mode', [])
        self.assertFalse(a.__eq__(b))
        self.assertTrue(a.__ne__(b))

    def test_different_types_sortable(self):
        a = FakeGuardedLock('nsA', 'name', 'mode', [])
        b = OtherFakeLock('nsB', 'name', 'mode', [])
        self.assertTrue(a < b)
        self.assertFalse(b < a)
        self.assertEqual([a, b], sorted([b, a]))

    @permutations((
        (('nsA', 'nameA', 'mode'), ('nsB', 'nameA', 'mode')),
        (('nsA', 'nameA', 'mode'), ('nsA', 'nameB', 'mode')),
    ))
    def test_less_than(self, a, b):
        ns_a, name_a, mode_a = a
        ns_b, name_b, mode_b = b
        b = FakeGuardedLock(ns_b, name_b, mode_b, [])
        a = FakeGuardedLock(ns_a, name_a, mode_a, [])
        self.assertLess(a, b)

    def test_equality(self):
        a = FakeGuardedLock('ns', 'name', 'mode', [])
        b = FakeGuardedLock('ns', 'name', 'mode', [])
        self.assertEqual(a, b)

    def test_mode_used_for_equality(self):
        a = FakeGuardedLock('nsA', 'nameA', 'modeA', [])
        b = FakeGuardedLock('nsA', 'nameA', 'modeB', [])
        self.assertNotEqual(a, b)

    def test_mode_ignored_for_sorting(self):
        a = FakeGuardedLock('nsA', 'nameA', 'modeA', [])
        b = FakeGuardedLock('nsA', 'nameA', 'modeB', [])
        self.assertFalse(a < b)
        self.assertFalse(b < a)

    def test_acquire_and_release(self):
        log = []
        expected = [('acquire', 'ns', 'name', 'mode'),
                    ('release', 'ns', 'name', 'mode')]
        lock = FakeGuardedLock('ns', 'name', 'mode', log)
        lock.acquire()
        self.assertEqual(expected[:1], log)
        lock.release()
        self.assertEqual(expected, log)


class TestAborting(VdsmTestCase):

    def test_aborting_flow(self):
        aborting = Aborting(5)
        for i in range(5):
            self.assertEqual(aborting(), False)
        self.assertEqual(aborting(), True)
