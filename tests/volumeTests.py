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

from testrunner import VdsmTestCase as TestCaseBase

from storage import outOfProcess
from storage.fileVolume import FileVolume


class FileDomainMockObject(object):
    def __init__(self, repoPath, sdUUID):
        self.repoPath = repoPath
        self.sdUUID = sdUUID

    def _getRepoPath(self):
        return self.repoPath

    @property
    def oop(self):
        return outOfProcess.getProcessPool(self.sdUUID)


class FileVolumeGetVSizeTest(TestCaseBase):
    VOLSIZE = 1024
    SDBLKSZ = 512

    def setUp(self):
        self.repoPath = tempfile.mkdtemp()

        self.sdUUID = str(uuid.uuid4())
        self.imgUUID = str(uuid.uuid4())
        self.volUUID = str(uuid.uuid4())

        imgPath = os.path.join(self.repoPath, self.sdUUID, "images",
                               self.imgUUID)
        volPath = os.path.join(imgPath, self.volUUID)

        os.makedirs(imgPath)
        open(volPath, "w").truncate(self.VOLSIZE * self.SDBLKSZ)
        self.sdobj = FileDomainMockObject(self.repoPath, self.sdUUID)

    def tearDown(self):
        shutil.rmtree(self.repoPath)

    def test(self):
        volSize = FileVolume.getVSize(self.sdobj, self.imgUUID,
                                      self.volUUID)
        assert volSize == self.VOLSIZE
