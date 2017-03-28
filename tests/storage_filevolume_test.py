#
# Copyright 2012 Red Hat, Inc.
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

from testlib import VdsmTestCase as TestCaseBase
from vdsm.config import config
from vdsm.constants import GIB
from vdsm.storage import constants as sc
import storage.fileVolume as fileVolume


class GetDomUuidFromVolumePathTests(TestCaseBase):
    def test(self):
        testPath = os.path.join(config.get('irs', 'repository'),
                                "spUUID/sdUUID/images/imgUUID/volUUID")
        self.assertEqual(fileVolume.getDomUuidFromVolumePath(testPath),
                         "sdUUID")


class TestFileVolumeManifest(TestCaseBase):

    def test_max_size_raw(self):
        max_size = fileVolume.FileVolume.max_size(1 * GIB, sc.RAW_FORMAT)
        # verify that max size equals to virtual size.
        self.assertEqual(max_size, 1 * GIB)

    def test_max_size_cow(self):
        max_size = fileVolume.FileVolume.max_size(10 * GIB, sc.COW_FORMAT)
        # verify that max size equals to virtual size with estimated cow
        # overhead, aligned to MiB.
        self.assertEqual(max_size, 11811160064)
