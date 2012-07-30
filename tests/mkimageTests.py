# -*- coding: utf-8 -*-
#kate: replace-tabs on; indent-width 4;
#
# Copyright 2012 Sandro Bonazzola <sandro.bonazzola@gmail.com>
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

"""
Tests for mkimage vdsm module.
@author: U{Sandro Bonazzola <mailto:sandro.bonazzola@gmail.com>}
@copyright: 2012 U{Sandro Bonazzola <mailto:sandro.bonazzola@gmail.com>}
"""

from base64 import b64encode
import os
from shutil import rmtree
from tempfile import mkdtemp

from testrunner import VdsmTestCase
from testValidation import checkSudo, ValidateRunningAsRoot

import storage
import mkimage


class MkimageTestCase(VdsmTestCase):
    """
    Tests for mkimage module.
    """
    #pylint: disable=R0904

    def setUp(self):
        """
        Prepares a workdir and set of files for the TestCase.
        Avoid requiring root privileges for os.chown call
        by mkimage._commonCleanFs.
        Avoid errors creating _P_PAYLOAD_IMAGES
        """
        #pylint: disable=W0212
        self.orig_mkimage = {
            "DISKIMAGE_USER": mkimage.DISKIMAGE_USER,
            "DISKIMAGE_GROUP": mkimage.DISKIMAGE_GROUP,
            "_P_PAYLOAD_IMAGES": mkimage._P_PAYLOAD_IMAGES
        }
        self.workdir = mkdtemp()
        self.img_dir = mkdtemp()
        mkimage.DISKIMAGE_USER = -1
        mkimage.DISKIMAGE_GROUP = -1
        mkimage._P_PAYLOAD_IMAGES = self.img_dir
        self.files = {}
        self.expected_results = {}
        for i in range(10):
            content = os.urandom(1024)
            self.expected_results["test_%d" % i] = content
            self.files["test_%d" % i] = b64encode(content)

    def tearDown(self):
        """
        Removes the workdir and its content when finished.
        Restore original values of mkimage constants.
        """
        rmtree(self.workdir)
        rmtree(self.img_dir)
        mkimage.DISKIMAGE_USER = self.orig_mkimage["DISKIMAGE_USER"]
        mkimage.DISKIMAGE_GROUP = self.orig_mkimage["DISKIMAGE_GROUP"]
        #pylint: disable=W0212
        mkimage._P_PAYLOAD_IMAGES = self.orig_mkimage["_P_PAYLOAD_IMAGES"]

    def _check_content(self):
        """
        Ensure that the workdir contains what we want
        """
        out_dir = os.listdir(self.workdir)
        self.assertEqual(len(out_dir), len(self.expected_results))
        for filename in self.expected_results:
            self.assertTrue(filename in out_dir)
            with open(os.path.join(self.workdir, filename), "r") as fd:
                content = fd.read()
                self.assertEqual(content, self.expected_results[filename])

    def test__decodeFilesIntoDir(self):
        """
        Tests mkimage._decodeFilesIntoDir
        """
        #pylint: disable=W0212
        mkimage._decodeFilesIntoDir(self.files, self.workdir)
        self._check_content()

    @ValidateRunningAsRoot
    def test_mkFloppyFs(self):
        """
        Tests mkimage.mkFloppyFs creating an image and checking its content.
        Requires root permissions for writing into the floppy image.
        """
        floppy = mkimage.mkFloppyFs("vmId_floppy", self.files)
        self.assertTrue(os.path.exists(floppy))
        m = storage.mount.Mount(floppy, self.workdir)
        m.mount(mntOpts='loop')
        try:
            self._check_content()
        finally:
            m.umount(force=True)
            os.unlink(floppy)

    def test_mkIsoFs(self):
        """
        Tests mkimage.mkIsoFs creating an image and checking its content
        """
        checkSudo(["mount", "-o", "loop", "somefile", "target"])
        checkSudo(["umount", "target"])
        iso_img = mkimage.mkIsoFs("vmId_iso", self.files)
        self.assertTrue(os.path.exists(iso_img))
        m = storage.mount.Mount(iso_img, self.workdir)
        m.mount(mntOpts='loop')
        try:
            self._check_content()
        finally:
            m.umount(force=True)
            os.unlink(iso_img)

    def test_removeFs(self):
        """
        Tests mkimage.removeFs creating an image and removing it.
        Check also that removeFs doesn't remove anything
        outside mkimage._P_PAYLOAD_IMAGES
        """
        checkSudo(["mount", "-o", "loop", "somefile", "target"])
        checkSudo(["umount", "target"])
        iso_img = mkimage.mkIsoFs("vmId_iso", self.files)
        self.assertTrue(os.path.exists(iso_img))
        mkimage.removeFs(iso_img)
        self.assertFalse(os.path.exists(iso_img))
        self.assertRaises(Exception, mkimage.removeFs, self.workdir)
