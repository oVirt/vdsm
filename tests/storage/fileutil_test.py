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
import stat

import pytest

from vdsm.storage import fileUtils
from testlib import VdsmTestCase
from testlib import expandPermutations, permutations
from testlib import namedTemporaryDir
from testlib import temporaryPath


class TestCreatedir(VdsmTestCase):

    def test_create_dirs_no_mode(self):
        with namedTemporaryDir() as base:
            path = os.path.join(base, "a", "b")
            self.assertFalse(os.path.isdir(path))
            fileUtils.createdir(path)
            self.assertTrue(os.path.isdir(path))

    def test_create_dirs_with_mode(self):
        with namedTemporaryDir() as base:
            path = os.path.join(base, "a", "b")
            mode = 0o700
            fileUtils.createdir(path, mode=mode)
            self.assertTrue(os.path.isdir(path))
            while path != base:
                pathmode = stat.S_IMODE(os.lstat(path).st_mode)
                self.assertEqual(pathmode, mode)
                path = os.path.dirname(path)

    @pytest.mark.skipif(os.geteuid() == 0, reason="requires unprivileged user")
    def test_create_raise_errors(self):
        with namedTemporaryDir() as base:
            path = os.path.join(base, "a", "b")
            self.assertRaises(OSError, fileUtils.createdir, path, 0o400)

    def test_directory_exists_no_mode(self):
        with namedTemporaryDir() as base:
            fileUtils.createdir(base)

    def test_directory_exists_other_mode(self):
        with namedTemporaryDir() as base:
            self.assertRaises(OSError, fileUtils.createdir, base, 0o755)

    def test_file_exists_with_mode(self):
        with namedTemporaryDir() as base:
            path = os.path.join(base, "file")
            with open(path, "w"):
                mode = stat.S_IMODE(os.lstat(path).st_mode)
                self.assertRaises(OSError, fileUtils.createdir, path, mode)

    def test_file_exists_no_mode(self):
        with namedTemporaryDir() as base:
            path = os.path.join(base, "file")
            with open(path, "w"):
                self.assertRaises(OSError, fileUtils.createdir, path)


class TestChown(VdsmTestCase):
    @pytest.mark.skipif(os.geteuid() != 0, reason="requires root")
    def test(self):
        targetId = 666
        with temporaryPath() as srcPath:
            fileUtils.chown(srcPath, targetId, targetId)
            stat = os.stat(srcPath)
            self.assertTrue(stat.st_uid == stat.st_gid == targetId)

    @pytest.mark.skipif(os.geteuid() != 0, reason="requires root")
    def testNames(self):
        # I convert to some id because I have no
        # idea what users are defined and what
        # there IDs are apart from root
        tmpId = 666
        with temporaryPath() as srcPath:
            fileUtils.chown(srcPath, tmpId, tmpId)
            stat = os.stat(srcPath)
            self.assertTrue(stat.st_uid == stat.st_gid == tmpId)

            fileUtils.chown(srcPath, "root", "root")
            stat = os.stat(srcPath)
            self.assertTrue(stat.st_uid == stat.st_gid == 0)


class TestCopyUserModeToGroup(VdsmTestCase):
    MODE_MASK = 0o777

    # format: initialMode, expectedMode
    modesList = [
        (0o770, 0o770), (0o700, 0o770), (0o750, 0o770), (0o650, 0o660),
    ]

    def testCopyUserModeToGroup(self):
        with temporaryPath() as path:
            for initialMode, expectedMode in self.modesList:
                os.chmod(path, initialMode)
                fileUtils.copyUserModeToGroup(path)
                self.assertEqual(os.stat(path).st_mode & self.MODE_MASK,
                                 expectedMode)


class TestAtomicSymlink(VdsmTestCase):

    def test_create_new(self):
        with namedTemporaryDir() as tmpdir:
            target = os.path.join(tmpdir, "target")
            link = os.path.join(tmpdir, "link")
            fileUtils.atomic_symlink(target, link)
            self.assertEqual(os.readlink(link), target)
            self.assertFalse(os.path.exists(link + ".tmp"))

    def test_keep_current(self):
        with namedTemporaryDir() as tmpdir:
            target = os.path.join(tmpdir, "target")
            link = os.path.join(tmpdir, "link")
            fileUtils.atomic_symlink(target, link)
            current = os.lstat(link)
            fileUtils.atomic_symlink(target, link)
            new = os.lstat(link)
            self.assertEqual(current, new)
            self.assertFalse(os.path.exists(link + ".tmp"))

    def test_replace_stale(self):
        with namedTemporaryDir() as tmpdir:
            target = os.path.join(tmpdir, "target")
            link = os.path.join(tmpdir, "link")
            fileUtils.atomic_symlink("stale", link)
            fileUtils.atomic_symlink(target, link)
            self.assertEqual(os.readlink(link), target)
            self.assertFalse(os.path.exists(link + ".tmp"))

    def test_replace_stale_temporary_link(self):
        with namedTemporaryDir() as tmpdir:
            target = os.path.join(tmpdir, "target")
            link = os.path.join(tmpdir, "link")
            tmp_link = link + ".tmp"
            fileUtils.atomic_symlink("stale", tmp_link)
            fileUtils.atomic_symlink(target, link)
            self.assertEqual(os.readlink(link), target)
            self.assertFalse(os.path.exists(tmp_link))

    def test_error_isfile(self):
        with namedTemporaryDir() as tmpdir:
            target = os.path.join(tmpdir, "target")
            link = os.path.join(tmpdir, "link")
            with open(link, 'w') as f:
                f.write('data')
            self.assertRaises(OSError, fileUtils.atomic_symlink, target, link)

    def test_error_isdir(self):
        with namedTemporaryDir() as tmpdir:
            target = os.path.join(tmpdir, "target")
            link = os.path.join(tmpdir, "link")
            os.mkdir(link)
            self.assertRaises(OSError, fileUtils.atomic_symlink, target, link)


@expandPermutations
class TestNormalizePath(VdsmTestCase):

    @permutations([
        # Remote paths without a port
        ("server:/path", "server:/path"),
        ("server://path", "server:/path"),
        ("server:///path", "server:/path"),
        ("server:/path/", "server:/path"),
        ("server:/pa:th", "server:/pa:th"),
        ("server:/path//", "server:/path"),
        ("server:/", "server:/"),
        ("server://", "server:/"),
        ("server:///", "server:/"),
        ("12.34.56.78:/", "12.34.56.78:/"),
        ("[2001:db8::60fe:5bf:febc:912]:/", "[2001:db8::60fe:5bf:febc:912]:/"),
        ("server:01234:/", "server:01234:"),

        # Remote paths with a port (relevant for cephfs mounts)
        ("server:6789:/path", "server:6789:/path"),
        ("server:6789:/", "server:6789:/"),

        # Local paths
        ("/path/to/device", "/path/to/device"),
        ("/path/to//device/", "/path/to/device"),

        # Other paths
        ("proc", "proc"),
        ("path//to///device/", "path/to/device"),
    ])
    def test_normalize_path_equals(self, path, normalized_path):
        self.assertEqual(normalized_path, fileUtils.normalize_path(path))
