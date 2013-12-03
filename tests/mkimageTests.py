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
import stat
from shutil import rmtree
from tempfile import mkdtemp

from nose.plugins.skip import SkipTest
from testrunner import VdsmTestCase, permutations, expandPermutations
from testValidation import checkSudo, ValidateRunningAsRoot

from vdsm.utils import execCmd
import storage
import mkimage


@expandPermutations
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
        self.subdir = os.path.join('a', 'subdirectory', 'for', 'testing')
        for i in range(5):
            content = os.urandom(1024)
            filename = "test_%d" % i
            longpath = os.path.join(self.subdir, filename)
            self.expected_results[filename] = content
            self.files[filename] = b64encode(content)
            self.expected_results[longpath] = content
            self.files[longpath] = b64encode(content)

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

    def _check_permissions(self, filepath, permsMask):
        """
        Ensure the file at `filepath' has the permissions coherent
        with the given mask.
        The mask may specifiy the required presence, or absence, of a
        permission bit.
        """
        data = os.stat(filepath)
        if stat.S_ISREG(data.st_mode):
            for perm, expected in permsMask:
                self.assertEqual(bool(data.st_mode & perm), expected,
                                 '%s: %s' % (filepath, oct(data.st_mode)))

    def _check_content(self, checkPerms=True):
        """
        Ensure that the workdir contains what we want
        """
        out_dir = os.listdir(self.workdir)
        out_subdir = os.listdir(os.path.join(self.workdir, self.subdir))
        self.assertEqual(len(out_dir) - 1, len(self.expected_results) / 2)
        self.assertEqual(len(out_subdir), len(self.expected_results) / 2)
        for filename in self.expected_results:
            if os.path.basename(filename) == filename:
                self.assertIn(filename, out_dir)
            else:
                self.assertIn(os.path.basename(filename), out_subdir)
            filepath = os.path.join(self.workdir, filename)
            if checkPerms:
                self._check_permissions(filepath,
                                        ((stat.S_IRGRP, True),
                                         (stat.S_IWGRP, False),
                                         (stat.S_IXGRP, False)))
                self._check_permissions(filepath,
                                        ((stat.S_IRUSR, True),
                                         (stat.S_IWUSR, True),
                                         (stat.S_IXUSR, False)))
                self._check_permissions(filepath,
                                        ((stat.S_IROTH, False),
                                         (stat.S_IWOTH, False),
                                         (stat.S_IXOTH, False)))
            with open(filepath, "r") as fd:
                content = fd.read()
                self.assertEqual(content, self.expected_results[filename])

    def _check_label(self, imgPath, label):
        """
        Ensures filesystem contains the desired label
        """
        if label is None:
            return
        cmd = ['blkid', '-s', 'LABEL', imgPath]
        try:
            (ret, out, err) = execCmd(cmd, raw=True)
        except OSError:
            raise SkipTest("cannot execute blkid")

        self.assertEqual(ret, 0)
        partitions = out.rpartition('LABEL=')
        self.assertEqual(len(partitions), 3)
        self.assertEqual(partitions[2].strip(), '"' + label + '"')

    def test__decodeFilesIntoDir(self):
        """
        Tests mkimage._decodeFilesIntoDir
        """
        #pylint: disable=W0212
        mkimage._decodeFilesIntoDir(self.files, self.workdir)
        self._check_content()

    @ValidateRunningAsRoot
    @permutations([[None], ['FSLABEL']])
    def test_mkFloppyFs(self, label):
        """
        Tests mkimage.mkFloppyFs creating an image and checking its content.
        Requires root permissions for writing into the floppy image.
        """
        floppy = mkimage.mkFloppyFs("vmId_floppy", self.files, label)
        self.assertTrue(os.path.exists(floppy))
        m = storage.mount.Mount(floppy, self.workdir)
        m.mount(mntOpts='loop')
        try:
            self._check_content(checkPerms=False)
            self._check_label(floppy, label)
        finally:
            m.umount(force=True)
            os.unlink(floppy)

    @permutations([[None], ['fslabel']])
    def test_mkIsoFs(self, label):
        """
        Tests mkimage.mkIsoFs creating an image and checking its content
        """
        checkSudo(["mount", "-o", "loop", "somefile", "target"])
        checkSudo(["umount", "target"])
        iso_img = mkimage.mkIsoFs("vmId_iso", self.files, label)
        self.assertTrue(os.path.exists(iso_img))
        m = storage.mount.Mount(iso_img, self.workdir)
        m.mount(mntOpts='loop')
        try:
            self._check_content()
            self._check_label(iso_img, label)
            self._check_permissions(iso_img,
                                    ((stat.S_IRUSR, True),
                                     (stat.S_IWUSR, True),
                                     (stat.S_IXUSR, False)))
            self._check_permissions(iso_img,
                                    ((stat.S_IRGRP, True),
                                     (stat.S_IWGRP, False),
                                     (stat.S_IXGRP, False)))
            self._check_permissions(iso_img,
                                    ((stat.S_IROTH, False),
                                     (stat.S_IWOTH, False),
                                     (stat.S_IXOTH, False)))
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
