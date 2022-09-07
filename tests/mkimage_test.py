# SPDX-FileCopyrightText: Sandro Bonazzola <sandro.bonazzola@gmail.com>
# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

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

import six

from monkeypatch import Patch
from testlib import VdsmTestCase, permutations, expandPermutations
from testValidation import checkSudo, ValidateRunningAsRoot

from vdsm.common.commands import run
from vdsm.common.fileutils import rm_file
from vdsm.constants import EXT_MKFS_MSDOS
from vdsm.storage import mount
from vdsm.storage.mount import MountError
from vdsm import mkimage


@expandPermutations
class MkimageTestCase(VdsmTestCase):
    """
    Tests for mkimage module.
    """
    # pylint: disable=R0904

    def setUp(self):
        """
        Prepares a workdir and set of files for the TestCase.
        Avoid requiring root privileges for os.chown call
        by mkimage._commonCleanFs.
        Avoid errors creating _P_PAYLOAD_IMAGES
        """
        self.tempdir = mkdtemp(prefix="vdsm-mkimage-tests")
        self.workdir = os.path.join(self.tempdir, "work")
        os.mkdir(self.workdir)
        self.img_dir = os.path.join(self.tempdir, "images")
        os.mkdir(self.img_dir)
        self.files = {}
        self.expected_results = {}
        self.subdir = os.path.join('a', 'subdirectory', 'for', 'testing')
        for i in range(5):
            content = os.urandom(1024)
            encoded_content = b64encode(content)

            # the base64-encoded file contents are actually read from the
            # domain XML, so they're unicode strings in py3
            if six.PY3:
                encoded_content = encoded_content.decode("utf-8")

            filename = "test_%d" % i
            longpath = os.path.join(self.subdir, filename)
            self.expected_results[filename] = content
            self.files[filename] = encoded_content
            self.expected_results[longpath] = content
            self.files[longpath] = encoded_content

        self.patch = Patch([
            (mkimage, "DISKIMAGE_USER", -1),
            (mkimage, "DISKIMAGE_GROUP", -1),
            (mkimage, "_P_PAYLOAD_IMAGES", self.img_dir),
        ])

        # Must be last; if setUp fails, tearDown is not invoked
        self.patch.apply()

    def tearDown(self):
        """
        Removes the workdir and its content when finished.
        Restore original values of mkimage constants.
        """
        self.patch.revert()
        rmtree(self.tempdir)

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
                                        ((stat.S_IRUSR, True),
                                         (stat.S_IWUSR, True),
                                         (stat.S_IXUSR, False)))
                self._check_permissions(filepath,
                                        ((stat.S_IRGRP, True),
                                         (stat.S_IWGRP, False),
                                         (stat.S_IXGRP, False)))
                self._check_permissions(filepath,
                                        ((stat.S_IROTH, False),
                                         (stat.S_IWOTH, False),
                                         (stat.S_IXOTH, False)))
            with open(filepath, "rb") as fd:
                content = fd.read()
                self.assertEqual(content, self.expected_results[filename])

    def _check_label(self, imgPath, label):
        """
        Ensures filesystem contains the desired label
        """
        if label is None:
            return
        cmd = ['blkid', '-s', 'LABEL', imgPath]
        out = run(cmd)

        partitions = out.rpartition(b'LABEL=')
        self.assertEqual(len(partitions), 3)
        self.assertEqual(partitions[2].strip(), b'"' + label.encode() + b'"')

    def test__decodeFilesIntoDir(self):
        """
        Tests mkimage._decodeFilesIntoDir
        """
        # pylint: disable=W0212
        mkimage._decodeFilesIntoDir(self.files, self.workdir)
        self._check_content()

    @ValidateRunningAsRoot
    @permutations([['vfat'], ['auto']])
    def test_injectFilesToFs(self, fstype):
        """
        Tests mkimage.injectFilesToFs creating an image and checking its
        content. Requires root permissions for writing into the floppy image.
        """
        floppy = mkimage.getFileName("vmId_inject")
        command = [EXT_MKFS_MSDOS, '-C', floppy, '1440']
        try:
            run(command)

            mkimage.injectFilesToFs(floppy, self.files, fstype)

            self.assertTrue(os.path.exists(floppy))
            m = mount.Mount(floppy, self.workdir)
            m.mount(mntOpts='loop')
            try:
                self._check_content(checkPerms=False)
            finally:
                m.umount()
        finally:
            rm_file(floppy)

    @ValidateRunningAsRoot
    def test_injectFilesToFs_wrongfs(self):
        """
        Tests for failure mkimage.injectFilesToFs when wrong fstype is
        specified. Requires root permissions for mounting the image.
        """
        floppy = mkimage.getFileName("vmId_inject")
        command = [EXT_MKFS_MSDOS, '-C', floppy, '1440']
        try:
            run(command)

            with self.assertRaises(MountError):
                mkimage.injectFilesToFs(floppy, self.files, 'ext3')
        finally:
            rm_file(floppy)

    @ValidateRunningAsRoot
    @permutations([[None], ['FSLABEL']])
    def test_mkFloppyFs(self, label):
        """
        Tests mkimage.mkFloppyFs creating an image and checking its content.
        Requires root permissions for writing into the floppy image.
        """
        floppy = mkimage.mkFloppyFs("vmId_floppy", self.files, label)
        self.assertTrue(os.path.exists(floppy))
        m = mount.Mount(floppy, self.workdir)
        m.mount(mntOpts='loop')
        try:
            self._check_content(checkPerms=False)
            self._check_label(floppy, label)
        finally:
            m.umount()

    @ValidateRunningAsRoot
    @permutations([[None], ['FSLABEL']])
    def test_mkFloppyFs_overwrite(self, label):
        """
        Test that mkimage.mkFloppyFs handle situation when the floppy image
        already exists.

        Requires root permissions for writing into the floppy image.
        """
        floppy = mkimage.mkFloppyFs("vmId_floppy", self.files, label)
        self.assertTrue(os.path.exists(floppy))
        # Now try again with the floppy image already in place
        floppy = mkimage.mkFloppyFs("vmId_floppy", self.files, label)
        self.assertTrue(os.path.exists(floppy))
        m = mount.Mount(floppy, self.workdir)
        m.mount(mntOpts='loop')
        try:
            self._check_content(checkPerms=False)
            self._check_label(floppy, label)
        finally:
            m.umount()

    @ValidateRunningAsRoot
    @permutations([[None], ['fslabel']])
    def test_mkIsoFs(self, label):
        """
        Tests mkimage.mkIsoFs creating an image and checking its content
        """
        iso_img = mkimage.mkIsoFs("vmId_iso", self.files, label)
        self.assertTrue(os.path.exists(iso_img))
        m = mount.Mount(iso_img, self.workdir)
        m.mount(mntOpts='loop')
        try:
            self._check_content()
            self._check_label(iso_img, label)
        finally:
            m.umount()

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
