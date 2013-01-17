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

import tempfile
import os
import time

import testValidation
from testrunner import VdsmTestCase as TestCaseBase
from nose.plugins.skip import SkipTest

from vdsm import utils

try:
    import parted_utils as putils
except Exception:
    raise SkipTest("unable to import pyparted modules.")


ONE_MB_IN_BYTES = 1048576
FILE_SIZE_MB = 100
PART_SIZE_MB = 50
FREE_SIZE = 50 * ONE_MB_IN_BYTES
PART_END_SIZE = 47 * ONE_MB_IN_BYTES


class PartedUtilsTests(TestCaseBase):
    tempFilePath = None
    devPath = None

    @testValidation.ValidateRunningAsRoot
    def setUp(self):
        self.assertFalse(self.tempFilePath and self.devPath)

        tempFd, self.tempFilePath = tempfile.mkstemp()
        os.close(tempFd)

        cmd = ['dd', 'if=/dev/zero', 'of=%s' % self.tempFilePath,
               'bs=%sM' % FILE_SIZE_MB, 'count=1']
        rc, out, err = utils.execCmd(cmd)
        self.assertEquals(rc, 0)
        cmd = ['losetup', '-f', '--show', self.tempFilePath]
        rc, out, err = utils.execCmd(cmd)
        self.assertEquals(rc, 0)
        self.devPath = out[0].strip()

    @testValidation.ValidateRunningAsRoot
    def tearDown(self):
        self.assertTrue(self.tempFilePath and self.devPath)

        time.sleep(1)  # wait for syncing
        cmd = ['losetup', '-d', self.devPath]
        rc, out, err = utils.execCmd(cmd)
        self.assertEquals(rc, 0)
        os.unlink(self.tempFilePath)

        self.tempFilePath = None
        self.devPath = None

    def _empty_test(self):
        self.assertFalse(putils.getDevicePartedInfo('/dev/abcdef'))

    def _blank_dev_test(self):
        info = putils.getDevicePartedInfo(self.devPath)
        self.assertTrue(info['sectorSize'] >= 0)
        self.assertFalse(info['type'])
        self.assertFalse(info['partitions'])
        self.assertFalse(info['freeSpaceRegions'])

    def _parted_dev_test(self):
        cmd = ['parted', '-s', self.devPath, 'mktable', 'gpt']
        rc, out, err = utils.execCmd(cmd)
        self.assertEquals(rc, 0)
        cmd = ['parted', '-s', self.devPath,
               'mkpart', 'primary', '0', '%sMB' % PART_SIZE_MB]
        rc, out, err = utils.execCmd(cmd)
        self.assertEquals(rc, 0)
        cmd = ['parted', '-s', self.devPath, 'set', '1', 'boot', 'on']
        rc, out, err = utils.execCmd(cmd)
        self.assertEquals(rc, 0)
        time.sleep(1)  # wait for syncing

        info = putils.getDevicePartedInfo(self.devPath)
        self.assertTrue(info['sectorSize'] >= 0)
        self.assertEquals(info['type'], 'gpt')
        self.assertTrue(info['freeSpaceRegions'][0][3] >= FREE_SIZE)
        partName = '%sp1' % self.devPath
        partInfo = info['partitions'].get(partName)
        self.assertTrue(partInfo)
        self.assertEquals(partInfo[0], ['boot'])
        self.assertTrue(partInfo[1] >= 0)
        self.assertTrue((partInfo[2] * info['sectorSize']) >= PART_END_SIZE)

    @testValidation.ValidateRunningAsRoot
    def test_getDevicePartedInfo(self):
        self._empty_test()
        self._blank_dev_test()
        self._parted_dev_test()
