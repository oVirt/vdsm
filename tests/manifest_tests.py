# Copyright 2015 Red Hat, Inc.
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
import uuid

from testlib import VdsmTestCase, namedTemporaryDir
from monkeypatch import MonkeyPatchScope
from storagefakelib import FakeLVM

from storage import sd, blockSD, fileSD

MDSIZE = 524288
VOLSIZE = 1048576


class FileManifestTests(VdsmTestCase):
    MDSIZE = 524288

    def test_getreaddelay(self):
        with namedTemporaryDir() as tmpdir:
            manifest = self.make_manifest(tmpdir)
            self.assertIsInstance(manifest.getReadDelay(), float)

    def test_getvsize(self):
        with namedTemporaryDir() as tmpdir:
            manifest = self.make_manifest(tmpdir)
            imguuid, voluuid = make_volume(manifest.domaindir, VOLSIZE)
            self.assertEqual(VOLSIZE, manifest.getVSize(imguuid, voluuid))

    def test_getisodomainimagesdir(self):
        with namedTemporaryDir() as tmpdir:
            manifest = self.make_manifest(tmpdir)
            isopath = os.path.join(manifest.domaindir, sd.DOMAIN_IMAGES,
                                   sd.ISO_IMAGE_UUID)
            self.assertEquals(isopath, manifest.getIsoDomainImagesDir())

    def test_getmdpath(self):
        with namedTemporaryDir() as tmpdir:
            manifest = self.make_manifest(tmpdir)
            mdpath = os.path.join(manifest.domaindir, sd.DOMAIN_META_DATA)
            self.assertEquals(mdpath, manifest.getMDPath())

    def make_manifest(self, tmpdir, metadata=None):
        sduuid = str(uuid.uuid4())
        domain_path = os.path.join(tmpdir, sduuid)
        make_fake_metafile(self.get_metafile_path(domain_path))
        if metadata is None:
            metadata = dict()
        manifest = fileSD.FileStorageDomainManifest(domain_path, metadata)
        return manifest

    def get_metafile_path(self, domaindir):
        return os.path.join(domaindir, sd.DOMAIN_META_DATA, sd.METADATA)


class BlockManifestTests(VdsmTestCase):

    def test_getreaddelay(self):
        with namedTemporaryDir() as tmpdir:
            manifest = self.make_manifest()
            vg_name = manifest.sdUUID
            lvm = FakeLVM(tmpdir)
            make_fake_metafile(lvm.lvPath(vg_name, 'metadata'))

            with MonkeyPatchScope([(blockSD, 'lvm', lvm)]):
                self.assertIsInstance(manifest.getReadDelay(), float)

    def test_getvsize_active_lv(self):
        # Tests the path when the device file is present
        with namedTemporaryDir() as tmpdir:
            manifest = self.make_manifest()
            vg_name = manifest.sdUUID
            lvm = FakeLVM(tmpdir)
            self.make_vg(lvm, vg_name)
            lv_name = str(uuid.uuid4())
            lvm.createLV(vg_name, lv_name, VOLSIZE)
            lvm.fake_lv_symlink_create(vg_name, lv_name)
            with MonkeyPatchScope([(blockSD, 'lvm', lvm)]):
                self.assertEqual(VOLSIZE,
                                 manifest.getVSize('<imgUUID>', lv_name))

    def test_getvsize_inactive_lv(self):
        # Tests the path when the device file is not present
        with namedTemporaryDir() as tmpdir:
            manifest = self.make_manifest()
            vg_name = manifest.sdUUID
            lvm = FakeLVM(tmpdir)
            self.make_vg(lvm, vg_name)
            lv_name = str(uuid.uuid4())
            lvm.createLV(vg_name, lv_name, VOLSIZE)
            with MonkeyPatchScope([(blockSD, 'lvm', lvm)]):
                self.assertEqual(VOLSIZE,
                                 manifest.getVSize('<imgUUID>', lv_name))

    def make_manifest(self, metadata=None):
        sduuid = str(uuid.uuid4())
        if metadata is None:
            metadata = dict()
        manifest = blockSD.BlockStorageDomainManifest(sduuid, metadata)
        return manifest

    def make_vg(self, fakeLvm, vg_name):
        devices = self.get_device_list(10)
        fakeLvm.createVG(vg_name, devices, blockSD.STORAGE_UNREADY_DOMAIN_TAG,
                         blockSD.VG_METADATASIZE)

    def get_device_list(self, count):
        return ['/dev/mapper/{0}'.format(os.urandom(16).encode('hex'))
                for _ in range(count)]


def make_fake_metafile(metafile):
    os.makedirs(os.path.dirname(metafile))
    with open(metafile, "w") as f:
        f.truncate(MDSIZE)


def make_volume(domaindir, size):
    imguuid = str(uuid.uuid4())
    voluuid = str(uuid.uuid4())
    imgpath = os.path.join(domaindir, "images", imguuid)
    volpath = os.path.join(imgpath, voluuid)
    os.makedirs(imgpath)
    with open(volpath, "w") as f:
        f.truncate(size)
    return imguuid, voluuid
