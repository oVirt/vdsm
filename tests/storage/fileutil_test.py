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

from __future__ import absolute_import
from __future__ import division

import os
import stat

import pytest

from vdsm.storage import fileUtils
from testlib import VdsmTestCase
from testlib import expandPermutations, permutations
from testlib import namedTemporaryDir
from testlib import temporaryPath
from . marks import xfail_python37, requires_unprivileged_user, requires_root


class TestCreatedir(VdsmTestCase):

    def test_create_dirs_no_mode(self):
        with namedTemporaryDir() as base:
            path = os.path.join(base, "a", "b")
            assert not os.path.isdir(path)
            fileUtils.createdir(path)
            assert os.path.isdir(path)

    def test_create_dirs_with_mode(self):
        with namedTemporaryDir() as base:
            path = os.path.join(base, "a", "b")
            mode = 0o700
            fileUtils.createdir(path, mode=mode)
            assert os.path.isdir(path)
            actual_mode = stat.S_IMODE(os.lstat(path).st_mode)
            assert actual_mode == mode

    @xfail_python37
    def test_create_dirs_with_mode_intermediate(self):
        with namedTemporaryDir() as base:
            intermediate = os.path.join(base, "a")
            path = os.path.join(intermediate, "b")
            mode = 0o700
            fileUtils.createdir(path, mode=mode)
            assert os.path.isdir(path)
            actual_mode = stat.S_IMODE(os.lstat(intermediate).st_mode)
            assert actual_mode == mode

    @xfail_python37
    @requires_unprivileged_user
    def test_create_raise_errors(self):
        with namedTemporaryDir() as base:
            path = os.path.join(base, "a", "b")
            with pytest.raises(OSError):
                fileUtils.createdir(path, mode=0o400)

    def test_directory_exists_no_mode(self):
        with namedTemporaryDir() as base:
            fileUtils.createdir(base)

    def test_directory_exists_other_mode(self):
        with namedTemporaryDir() as base:
            with pytest.raises(OSError):
                fileUtils.createdir(base, mode=0o755)

    def test_file_exists_with_mode(self):
        with namedTemporaryDir() as base:
            path = os.path.join(base, "file")
            with open(path, "w"):
                mode = stat.S_IMODE(os.lstat(path).st_mode)
                with pytest.raises(OSError):
                    fileUtils.createdir(path, mode=mode)

    def test_file_exists_no_mode(self):
        with namedTemporaryDir() as base:
            path = os.path.join(base, "file")
            with open(path, "w"):
                with pytest.raises(OSError):
                    fileUtils.createdir(path)


class TestChown(VdsmTestCase):
    @requires_root
    def test(self):
        targetId = 666
        with temporaryPath() as srcPath:
            fileUtils.chown(srcPath, targetId, targetId)
            stat = os.stat(srcPath)
            assert stat.st_uid == stat.st_gid == targetId

    @requires_root
    def testNames(self):
        # I convert to some id because I have no
        # idea what users are defined and what
        # there IDs are apart from root
        tmpId = 666
        with temporaryPath() as srcPath:
            fileUtils.chown(srcPath, tmpId, tmpId)
            stat = os.stat(srcPath)
            assert stat.st_uid == stat.st_gid == tmpId

            fileUtils.chown(srcPath, "root", "root")
            stat = os.stat(srcPath)
            assert stat.st_uid == stat.st_gid == 0


class TestAtomicSymlink(VdsmTestCase):

    def test_create_new(self):
        with namedTemporaryDir() as tmpdir:
            target = os.path.join(tmpdir, "target")
            link = os.path.join(tmpdir, "link")
            fileUtils.atomic_symlink(target, link)
            assert os.readlink(link) == target
            assert not os.path.exists(link + ".tmp")

    def test_keep_current(self):
        with namedTemporaryDir() as tmpdir:
            target = os.path.join(tmpdir, "target")
            link = os.path.join(tmpdir, "link")
            fileUtils.atomic_symlink(target, link)
            fileUtils.atomic_symlink(target, link)
            assert os.readlink(link) == target
            assert not os.path.exists(link + ".tmp")

    def test_replace_stale(self):
        with namedTemporaryDir() as tmpdir:
            target = os.path.join(tmpdir, "target")
            link = os.path.join(tmpdir, "link")
            fileUtils.atomic_symlink("stale", link)
            fileUtils.atomic_symlink(target, link)
            assert os.readlink(link) == target
            assert not os.path.exists(link + ".tmp")

    def test_replace_stale_temporary_link(self):
        with namedTemporaryDir() as tmpdir:
            target = os.path.join(tmpdir, "target")
            link = os.path.join(tmpdir, "link")
            tmp_link = link + ".tmp"
            fileUtils.atomic_symlink("stale", tmp_link)
            fileUtils.atomic_symlink(target, link)
            assert os.readlink(link) == target
            assert not os.path.exists(tmp_link)

    def test_error_isfile(self):
        with namedTemporaryDir() as tmpdir:
            target = os.path.join(tmpdir, "target")
            link = os.path.join(tmpdir, "link")
            with open(link, 'w') as f:
                f.write('data')
            with pytest.raises(OSError):
                fileUtils.atomic_symlink(target, link)

    def test_error_isdir(self):
        with namedTemporaryDir() as tmpdir:
            target = os.path.join(tmpdir, "target")
            link = os.path.join(tmpdir, "link")
            os.mkdir(link)
            with pytest.raises(OSError):
                fileUtils.atomic_symlink(target, link)


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
        assert normalized_path == fileUtils.normalize_path(path)
