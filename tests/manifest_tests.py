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


class FileManifestTests(VdsmTestCase):
    MDSIZE = 524288

    def test_getreaddelay(self):
        with namedTemporaryDir() as tmpdir:
            manifest = self.make_manifest(tmpdir)
            self.assertIsInstance(manifest.getReadDelay(), float)

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

    def make_manifest(self, metadata=None):
        sduuid = str(uuid.uuid4())
        if metadata is None:
            metadata = dict()
        manifest = blockSD.BlockStorageDomainManifest(sduuid, metadata)
        return manifest


def make_fake_metafile(metafile):
    os.makedirs(os.path.dirname(metafile))
    with open(metafile, "w") as f:
        f.truncate(MDSIZE)
