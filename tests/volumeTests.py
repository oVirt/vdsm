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
import shutil
import tempfile
import uuid

from testlib import VdsmTestCase as TestCaseBase

from storage import outOfProcess, fileSD


class FileDomainMockObject(fileSD.FileStorageDomain):
    def __init__(self, mountpoint, sdUUID):
        self.mountpoint = mountpoint
        self.sdUUID = sdUUID
        self.stat = None

    @property
    def oop(self):
        return outOfProcess.getProcessPool(self.sdUUID)


class FileVolumeGetVSizeTest(TestCaseBase):
    VOLSIZE = 1024
    SDBLKSZ = 512

    def setUp(self):
        self.mountpoint = tempfile.mkdtemp()

        self.sdUUID = str(uuid.uuid4())
        self.imgUUID = str(uuid.uuid4())
        self.volUUID = str(uuid.uuid4())

        imgPath = os.path.join(self.mountpoint, self.sdUUID, "images",
                               self.imgUUID)
        volPath = os.path.join(imgPath, self.volUUID)

        os.makedirs(imgPath)
        open(volPath, "w").truncate(self.VOLSIZE * self.SDBLKSZ)
        self.sdobj = FileDomainMockObject(self.mountpoint, self.sdUUID)

    def tearDown(self):
        shutil.rmtree(self.mountpoint)

    def test(self):
        volSize = int(self.sdobj.getVSize(self.imgUUID, self.volUUID) /
                      self.SDBLKSZ)
        assert volSize == self.VOLSIZE
