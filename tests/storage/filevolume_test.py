#
# Copyright 2012-2017 Red Hat, Inc.
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

from contextlib import contextmanager
import os

from storage.storagetestlib import fake_env
from testlib import make_uuid
from vdsm.constants import GIB
from vdsm.constants import MEGAB
from vdsm.storage import constants as sc
from vdsm.storage import fileVolume


class TestGetDomUuidFromVolumePath(object):
    def test(self):
        testPath = os.path.join(sc.REPO_DATA_CENTER,
                                "spUUID/sdUUID/images/imgUUID/volUUID")
        assert fileVolume.getDomUuidFromVolumePath(testPath) == "sdUUID"


class TestFileVolumeManifest(object):

    @contextmanager
    def make_volume(self, size, storage_type='file', format=sc.RAW_FORMAT):
        img_id = make_uuid()
        vol_id = make_uuid()
        with fake_env(storage_type) as env:
            env.make_volume(size, img_id, vol_id, vol_format=format)
            vol = env.sd_manifest.produceVolume(img_id, vol_id)
            yield vol

    def test_max_size_raw(self):
        max_size = fileVolume.FileVolume.max_size(1 * GIB, sc.RAW_FORMAT)
        # verify that max size equals to virtual size.
        assert max_size == 1 * GIB

    def test_max_size_cow(self):
        max_size = fileVolume.FileVolume.max_size(10 * GIB, sc.COW_FORMAT)
        # verify that max size equals to virtual size with estimated cow
        # overhead, aligned to MiB.
        assert max_size == 11811160064

    def test_optimal_size_raw(self):
        size = 5 * MEGAB
        with self.make_volume(size=size) as vol:
            assert vol.optimal_size() == size

    def test_optimal_size_cow(self):
        size = 5 * MEGAB
        with self.make_volume(size=size, format=sc.COW_FORMAT) as vol:
            assert vol.optimal_size() == vol.getVolumeSize() * sc.BLOCK_SIZE
