#
# Copyright 2012-2016 Red Hat, Inc.
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

import errno
import gc
import logging
import os
import re
import shutil
import stat
import time
import weakref

from contextlib import contextmanager

import pytest

from vdsm.common.osutils import get_umask
from vdsm.storage import constants as sc
from vdsm.storage import outOfProcess as oop
from vdsm.storage.exception import MiscDirCleanupFailure

from . import userstorage
from . marks import requires_root, requires_unprivileged_user
from . storagetestlib import chmod


@pytest.fixture
def oop_cleanup():
    yield
    oop.stop()


@pytest.fixture(
    params=[
        userstorage.PATHS["mount-512"],
        userstorage.PATHS["mount-4k"],
    ],
    ids=str,
)
def user_mount(request):
    storage = request.param
    if not storage.exists():
        pytest.xfail("{} storage not available".format(storage.name))
    tmpdir = os.path.join(storage.path, "tmp")
    os.mkdir(tmpdir)
    yield tmpdir
    shutil.rmtree(tmpdir)


# TODO: the following 2 tests use private instance variables that
#  should not be used by tests.
# oop.getProcessPool(pool)._ioproc
#   This is a private implementation detail of VDSM.
# proc._commthread
#   Is an implementation detail and is neither part of ioprocess,
#   nor it is part of ioprocess API.
# proc._commthread.getName()
#   The method is not part of ioprocess API.
# proc._commthread.name
#   The member is not part of ioprocess API.

def test_same_pool_name(oop_cleanup):
    pids = []
    for poolName in ["A", "A"]:
        proc = oop.getProcessPool(poolName)._ioproc
        name = proc._commthread.getName()
        pids.append(int(re.search(r'\d+', name).group()))

    assert pids[0] == pids[1]


def test_different_pool_name(oop_cleanup):
    pids = []
    for poolName in ["A", "B"]:
        proc = oop.getProcessPool(poolName)._ioproc
        name = proc._commthread.name
        pids.append(int(re.search(r'\d+', name).group()))

    assert pids[0] != pids[1]


def test_amount_of_instances_per_pool_name(oop_cleanup, monkeypatch):
    # TODO: This is very bad test, assuming the behavior
    #  of the gc module instead of testing our code behavior.
    # We can replace the 3 tests for above with:
    #   a) Test that we get the same instance when calling with
    #      same pool name (e.g. getProcPool("this") is getProcPool("this"))
    #   b) Test that we get different instance when calling with
    #      different pool name
    #      (e.g.  getProcPool("this") is not getProcPool("other"))
    #   c) Test that after idle timeout, calling getProcPool("other") and
    #   getProcPool("this"), we get a new instance of "this".
    monkeypatch.setattr(oop, 'IOPROC_IDLE_TIME', 0.5)
    poolA = "A"
    poolB = "B"
    wrapper = weakref.ref(oop.getProcessPool(poolA))
    ioproc = weakref.ref(oop.getProcessPool(poolA)._ioproc)
    oop.getProcessPool(poolA)
    time.sleep(oop.IOPROC_IDLE_TIME + 0.5)
    oop.getProcessPool(poolB)
    assert wrapper() is None
    gc.collect()
    try:
        assert ioproc() is None
    except AssertionError:
        logging.info("GARBAGE: %s", gc.garbage)
        refs = gc.get_referrers(ioproc())
        logging.info(refs)
        logging.info(gc.get_referrers(*refs))
        raise


# fileUtils APIs

def test_fileutils_fsyncPath(oop_cleanup, tmpdir):
    iop = oop.getProcessPool("test")

    # No easy way to test that we actually fsync this path.
    # Lets just call it to make sure it does not fail.
    iop.fileUtils.fsyncPath(str(tmpdir))

    with pytest.raises(OSError) as e:
        iop.fileUtils.fsyncPath(str(tmpdir.join("no such directory")))
    assert e.value.errno == errno.ENOENT


def test_fileutils_cleanupdir_success(oop_cleanup, tmpdir):
    iop = oop.getProcessPool("test")
    dirty = tmpdir.mkdir("dirty")

    d = dirty
    for i in range(2):
        d.join("file").write("")
        d = d.mkdir("dir")

    dirty_path = str(dirty)
    iop.fileUtils.cleanupdir(dirty_path)
    assert not os.path.exists(dirty_path)


def test_fileutils_cleanupdir_failed_no_such_dir(oop_cleanup, tmpdir):
    iop = oop.getProcessPool("test")
    no_such_path = str(tmpdir.join("no such directory"))

    # Test on a non-existing path without specify ignoreErrors, fail silently.
    iop.fileUtils.cleanupdir(no_such_path)

    # Test on a non-existing path while ignoring errors, fail silently.
    iop.fileUtils.cleanupdir(no_such_path, ignoreErrors=True)

    # Test on a non-existing path while raising errors.
    with pytest.raises(OSError) as e:
        iop.fileUtils.cleanupdir(no_such_path, ignoreErrors=False)
    assert e.value.errno == errno.ENOENT


@requires_unprivileged_user
def test_fileutils_cleanupdir_failed_due_to_permissions(oop_cleanup, tmpdir):
    iop = oop.getProcessPool("test")
    dirty = tmpdir.mkdir("dirty")

    # Create directory tree with a non-empty inner most directory.
    d = dirty
    for i in range(2):
        d = d.mkdir("dir")
        d.join("file").write("")

    outer_dir = str(dirty)
    inner_dir = str(d)

    # Change mode of the inner sub-directory to have no 'write' permissions.
    with chmod(inner_dir, 0o555):
        # Test 'cleanupdir' without specify ignoreErrors, fail silently.
        iop.fileUtils.cleanupdir(outer_dir)
        assert os.path.exists(outer_dir)

        # Test 'cleanupdir' while ignoring errors, fail silently.
        iop.fileUtils.cleanupdir(outer_dir, ignoreErrors=True)
        assert os.path.exists(outer_dir)

        # Test 'cleanupdir' while raising errors.
        with pytest.raises(MiscDirCleanupFailure):
            iop.fileUtils.cleanupdir(outer_dir, ignoreErrors=False)
        assert os.path.exists(outer_dir)

    # After changing to original permissions, "cleanupdir" should succeed!
    iop.fileUtils.cleanupdir(outer_dir, ignoreErrors=False)
    assert not os.path.exists(outer_dir)


@pytest.mark.parametrize("initial_mode, expected_mode", [
    (0o770, 0o770),
    (0o700, 0o770),
    (0o750, 0o770),
    (0o650, 0o660),
])
def test_fileutils_copyusermodetogroup(
        oop_cleanup, tmpdir, initial_mode, expected_mode):
    iop = oop.getProcessPool("test")
    f = tmpdir.join("file")
    f.write("")

    path = str(f)
    os.chmod(path, initial_mode)

    iop.fileUtils.copyUserModeToGroup(path)
    verify_file(path, mode=expected_mode)


def test_fileutils_createdir_default_mode(oop_cleanup, tmpdir):
    iop = oop.getProcessPool("test")
    path_intermediate = str(tmpdir.join("subdir1"))
    path_leaf = str(tmpdir.join("subdir1", "subdir2"))
    # The test describes the current behavior of ioprocess.fileUtils.createdir:
    # the default mode is 0o775 (depends on umask).
    default_mode = 0o775
    expected_mode = default_mode & ~get_umask()

    # Folder does not exist and is created successfully.
    iop.fileUtils.createdir(path_leaf)
    verify_directory(path_intermediate, mode=expected_mode)
    verify_directory(path_leaf, mode=expected_mode)


def test_fileutils_createdir_default_mode_dir_exists(oop_cleanup, tmpdir):
    iop = oop.getProcessPool("test")
    path = str(tmpdir.join("subdir"))
    # The test describes the current behavior of ioprocess.fileUtils.createdir:
    # the default mode is 0o775 (depends on umask).
    default_mode = 0o775
    expected_mode = default_mode & ~get_umask()

    os.mkdir(path)

    # Folder exists, mode matches, operation should do nothing.
    iop.fileUtils.createdir(path)
    verify_directory(path, mode=expected_mode)


def test_fileutils_createdir_default_mode_file_exists(oop_cleanup, tmpdir):
    iop = oop.getProcessPool("test")
    path = str(tmpdir.join("file"))

    open(path, "w").close()

    # Path exists and is a file, operation should fail.
    with pytest.raises(OSError) as e:
        iop.fileUtils.createdir(path)
    assert e.value.errno == errno.ENOTDIR


def test_fileutils_createdir_non_default_mode(oop_cleanup, tmpdir):
    iop = oop.getProcessPool("test")
    path_intermediate = str(tmpdir.join("subdir1"))
    path_leaf = str(tmpdir.join("subdir1", "subdir2"))
    mode = 0o766
    expected_mode = mode & ~get_umask()

    # Folder does not exist and is created successfully.
    iop.fileUtils.createdir(path_leaf, mode=mode)
    verify_directory(path_intermediate, mode=expected_mode)
    verify_directory(path_leaf, mode=expected_mode)


def test_fileutils_createdir_non_default_mode_dir_exists_same_mode(
        oop_cleanup, tmpdir):
    iop = oop.getProcessPool("test")
    path = str(tmpdir.join("subdir"))
    mode = 0o766
    expected_mode = mode & ~get_umask()

    os.mkdir(path, mode=mode)

    # Folder exists, mode matches, operation should do nothing.
    iop.fileUtils.createdir(path, mode=mode)
    verify_directory(path, mode=expected_mode)


def test_fileutils_createdir_non_default_mode_dir_exists_other_mode(
        oop_cleanup, tmpdir):
    iop = oop.getProcessPool("test")
    path = str(tmpdir.join("subdir"))
    mode = 0o766

    os.mkdir(path, mode=mode)

    # Folder exists, mode doesn't match, operation should fail.
    with pytest.raises(OSError) as e:
        iop.fileUtils.createdir(path, mode=0o666)
    assert e.value.errno == errno.EPERM


def test_fileutils_createdir_non_default_mode_file_exists(oop_cleanup, tmpdir):
    iop = oop.getProcessPool("test")
    path = str(tmpdir.join("file"))

    open(path, "w").close()
    mode = stat.S_IMODE(os.stat(path).st_mode)

    # Path exists and is a file, operation should fail.
    with pytest.raises(OSError) as e:
        iop.fileUtils.createdir(path, mode=mode)
    assert e.value.errno == errno.ENOTDIR


@requires_unprivileged_user
def test_fileutils_createdir_bad_permissions(oop_cleanup, tmpdir):
    iop = oop.getProcessPool("test")
    path = str(tmpdir.join("subdir1", "subdir2"))
    mode = 0o666

    # Should create the first folder in the path and fail on the second one.
    with pytest.raises(OSError) as e:
        iop.fileUtils.createdir(path, mode=mode)
    assert e.value.errno == errno.EACCES

    assert os.path.exists(str(tmpdir.join("subdir1")))
    assert not os.path.exists(str(tmpdir.join("subdir1", "subdir2")))


@pytest.mark.parametrize("orig_size, expected_size", [
    (sc.BLOCK_SIZE_4K - 1, sc.BLOCK_SIZE_4K),
    (sc.BLOCK_SIZE_4K, sc.BLOCK_SIZE_4K),
    (sc.BLOCK_SIZE_4K + 1, 2 * sc.BLOCK_SIZE_4K),
])
def test_fileutils_padtoblocksize(
        oop_cleanup, tmpdir, orig_size, expected_size):
    iop = oop.getProcessPool("test")
    path = str(tmpdir.join("file"))

    orig_data = b"x" * orig_size
    with open(path, "wb") as f:
        f.write(orig_data)

    iop.fileUtils.padToBlockSize(path)

    with open(path, "rb") as f:
        assert f.read(orig_size) == orig_data
        assert f.read() == b"\0" * (expected_size - orig_size)


DEFAULT_PERMISSIONS = os.R_OK | os.W_OK | os.X_OK
ACCESS_PARAMS_DEFAULT_PERMISSION_SUCCESS = [0o755, 0o700]
ACCESS_PARAMS_DEFAULT_PERMISSION_FAILURE = [0o300, 0o444, 0o500, 0o600]


@pytest.mark.parametrize("mode", ACCESS_PARAMS_DEFAULT_PERMISSION_SUCCESS)
@requires_unprivileged_user
def test_fileutils_validateaccess_directory_default_permission_success(
        oop_cleanup, tmpdir, mode):
    iop = oop.getProcessPool("test")
    path = str(tmpdir.mkdir("subdir"))

    with chmod(path, mode):
        assert os.access(path, DEFAULT_PERMISSIONS)
        iop.fileUtils.validateAccess(path)


@pytest.mark.parametrize("mode", ACCESS_PARAMS_DEFAULT_PERMISSION_FAILURE)
@requires_unprivileged_user
def test_fileutils_validateaccess_directory_default_permission_failure(
        oop_cleanup, tmpdir, mode):
    iop = oop.getProcessPool("test")
    path = str(tmpdir.mkdir("subdir"))

    with chmod(path, mode):
        assert not os.access(path, DEFAULT_PERMISSIONS)
        with pytest.raises(OSError) as e:
            iop.fileUtils.validateAccess(path)
        assert e.value.errno == errno.EACCES


@pytest.mark.parametrize("mode", ACCESS_PARAMS_DEFAULT_PERMISSION_SUCCESS)
@requires_unprivileged_user
def test_fileutils_validateaccess_file_default_permission_success(
        oop_cleanup, tmpdir, mode):
    iop = oop.getProcessPool("test")

    f = tmpdir.join("file")
    f.write("")
    path = str(f)

    with chmod(path, mode):
        assert os.access(path, DEFAULT_PERMISSIONS)
        iop.fileUtils.validateAccess(path)


@pytest.mark.parametrize("mode", ACCESS_PARAMS_DEFAULT_PERMISSION_FAILURE)
@requires_unprivileged_user
def test_fileutils_validateaccess_file_default_permission_failure(
        oop_cleanup, tmpdir, mode):
    iop = oop.getProcessPool("test")

    f = tmpdir.join("file")
    f.write("")
    path = str(f)

    with chmod(path, mode):
        assert not os.access(path, DEFAULT_PERMISSIONS)
        with pytest.raises(OSError) as e:
            iop.fileUtils.validateAccess(path)
        assert e.value.errno == errno.EACCES


ACCESS_PARAMS_NON_DEFAULT_PERMISSION_SUCCESS = [
    (0o755, os.R_OK),
    (0o744, os.W_OK),
    (0o755, os.X_OK),
    (0o300, os.W_OK | os.X_OK),
]
ACCESS_PARAMS_NON_DEFAULT_PERMISSION_FAILURE = [
    (0o300, os.R_OK),
    (0o444, os.W_OK),
    (0o400, os.X_OK),
    (0o300, os.R_OK | os.W_OK | os.X_OK),
]


@pytest.mark.parametrize(
    "mode, permissions", ACCESS_PARAMS_NON_DEFAULT_PERMISSION_SUCCESS)
@requires_unprivileged_user
def test_fileutils_validateaccess_directory_non_default_permission_success(
        oop_cleanup, tmpdir, mode, permissions):
    iop = oop.getProcessPool("test")
    path = str(tmpdir.mkdir("subdir"))

    with chmod(path, mode):
        assert os.access(path, permissions)
        iop.fileUtils.validateAccess(path, permissions)


@pytest.mark.parametrize(
    "mode, permissions", ACCESS_PARAMS_NON_DEFAULT_PERMISSION_FAILURE)
@requires_unprivileged_user
def test_fileutils_validateaccess_directory_non_default_permission_failure(
        oop_cleanup, tmpdir, mode, permissions):
    iop = oop.getProcessPool("test")
    path = str(tmpdir.mkdir("subdir"))

    with chmod(path, mode):
        assert not os.access(path, permissions)
        with pytest.raises(OSError) as e:
            iop.fileUtils.validateAccess(path, permissions)
        assert e.value.errno == errno.EACCES


@pytest.mark.parametrize(
    "mode, permissions", ACCESS_PARAMS_NON_DEFAULT_PERMISSION_SUCCESS)
@requires_unprivileged_user
def test_fileutils_validateaccess_file_non_default_permission_success(
        oop_cleanup, tmpdir, mode, permissions):
    iop = oop.getProcessPool("test")

    f = tmpdir.join("file")
    f.write("")
    path = str(f)

    with chmod(path, mode):
        assert os.access(path, permissions)
        iop.fileUtils.validateAccess(path, permissions)


@pytest.mark.parametrize(
    "mode, permissions", ACCESS_PARAMS_NON_DEFAULT_PERMISSION_FAILURE)
@requires_unprivileged_user
def test_fileutils_validateaccess_file_non_default_permission_failure(
        oop_cleanup, tmpdir, mode, permissions):
    iop = oop.getProcessPool("test")

    f = tmpdir.join("file")
    f.write("")
    path = str(f)

    with chmod(path, mode):
        assert not os.access(path, permissions)
        with pytest.raises(OSError) as e:
            iop.fileUtils.validateAccess(path, permissions)
        assert e.value.errno == errno.EACCES


def test_fileutils_pathexists(oop_cleanup, tmpdir):
    iop = oop.getProcessPool("test")

    # Test for null device.
    path = "/dev/null"
    assert iop.fileUtils.pathExists(path)

    # Test for existing directory.
    path = str(tmpdir)
    assert iop.fileUtils.pathExists(path)

    # Test for non-existing file (not yet created).
    f = tmpdir.join("file")
    path = str(f)
    assert not iop.fileUtils.pathExists(path)

    # Test for existing file.
    f.write("")
    assert iop.fileUtils.pathExists(path)


@requires_root
def test_fileutils_validateqemureadable_other_group(oop_cleanup, tmpdir):
    iop = oop.getProcessPool("test")

    f = tmpdir.join("file")
    f.write("")
    path = str(f)

    # Change mode to have 'other read' permissions.
    with chmod(path, 0o755):
        iop.fileUtils.validateQemuReadable(path)

    # Change mode to have no 'other read' permissions.
    with chmod(path, 0o750):
        with pytest.raises(OSError) as e:
            iop.fileUtils.validateQemuReadable(path)
        assert e.value.errno == errno.EACCES


@pytest.mark.parametrize("gid, mode", [
    (107, 0o750),   # qemu
    (36, 0o750),    # kvm
])
@requires_root
def test_fileutils_validateqemureadable_qemu_or_kvm_group(
        oop_cleanup, tmpdir, gid, mode):
    iop = oop.getProcessPool("test")

    f = tmpdir.join("file")
    f.write("")
    path = str(f)

    # Change owner (to either qemu or kvm).
    with chown(path, gid=gid):
        # Change mode to have 'group read' permissions.
        with chmod(path, mode):
            iop.fileUtils.validateQemuReadable(path)


# os APIs

ACCESS_PARAMS = [
    (0o755, os.R_OK, True),
    (0o300, os.R_OK, False),
    (0o744, os.W_OK, True),
    (0o444, os.W_OK, False),
    (0o755, os.X_OK, True),
    (0o400, os.X_OK, False),
    (0o300, os.W_OK | os.X_OK, True),
    (0o7300, os.R_OK | os.W_OK | os.X_OK, False),
]


@pytest.mark.parametrize("mode, permission, expected_result", ACCESS_PARAMS)
@requires_unprivileged_user
def test_os_access_file(
        oop_cleanup, tmpdir, mode, permission, expected_result):
    iop = oop.getProcessPool("test")

    f = tmpdir.join("file")
    f.write("")
    path = str(f)

    with chmod(path, mode):
        assert iop.os.access(path, permission) == expected_result


@pytest.mark.parametrize("mode, permission, expected_result", ACCESS_PARAMS)
@requires_unprivileged_user
def test_os_access_directory(
        oop_cleanup, tmpdir, mode, permission, expected_result):
    iop = oop.getProcessPool("test")

    d = tmpdir.mkdir("subdir")
    path = str(d)

    with chmod(path, mode):
        assert iop.os.access(path, permission) == expected_result


def test_os_chmod_file_failed_no_such_file(oop_cleanup, tmpdir):
    iop = oop.getProcessPool("test")
    new_mode = 0o444
    path = str(tmpdir.join("file"))

    # File does not exist, operation should fail.
    with pytest.raises(OSError) as e:
        iop.os.chmod(path, new_mode)
    assert e.value.errno == errno.ENOENT


def test_os_chmod_file(oop_cleanup, tmpdir):
    iop = oop.getProcessPool("test")
    new_mode = 0o444
    path = str(tmpdir.join("file"))

    open(path, "w").close()

    # File exists, operation should succeed.
    iop.os.chmod(path, new_mode)
    expected_mode = new_mode & ~get_umask()
    verify_file(path, mode=expected_mode)


def test_os_chmod_dir_failed_no_such_dir(oop_cleanup, tmpdir):
    iop = oop.getProcessPool("test")
    new_mode = 0o555
    path = str(tmpdir.join("subdir"))

    # Directory does not exist, operation should fail.
    with pytest.raises(OSError) as e:
        iop.os.chmod(path, new_mode)
    assert e.value.errno == errno.ENOENT


def test_os_chmod_dir(oop_cleanup, tmpdir):
    iop = oop.getProcessPool("test")
    new_mode = 0o555
    path = str(tmpdir.join("subdir"))

    tmpdir.mkdir("subdir")

    # Directory exists, operation should succeed.
    iop.os.chmod(path, new_mode)
    expected_mode = new_mode & ~get_umask()
    verify_directory(path, mode=expected_mode)


def test_os_link_failed_no_such_file(oop_cleanup, tmpdir):
    iop = oop.getProcessPool("test")
    path_src = str(tmpdir.join("file"))
    path_dst = path_src + ".link"

    # Source does not exist, operation fails.
    with pytest.raises(OSError) as e:
        iop.os.link(path_src, path_dst)
    assert e.value.errno == errno.ENOENT


def test_os_link(oop_cleanup, tmpdir):
    iop = oop.getProcessPool("test")
    path_src1 = str(tmpdir.join("file1"))
    path_src2 = str(tmpdir.join("file2"))
    path_dst = str(tmpdir.join("file.link"))

    open(path_src1, "w").close()
    open(path_src2, "w").close()

    # Link does not exist, created to point to "file1".
    iop.os.link(path_src1, path_dst)
    assert os.path.samefile(path_src1, path_dst)

    # Link exists, attempt  to override to point to "file2" should fail.
    with pytest.raises(OSError) as e:
        iop.os.link(path_src2, path_dst)
    assert e.value.errno == errno.EEXIST
    assert os.path.samefile(path_src1, path_dst)


def test_os_mkdir_default_mode(oop_cleanup, tmpdir):
    iop = oop.getProcessPool("test")
    path = str(tmpdir.join("subdir"))
    # The test describes the current behavior of ioprocess.os.mkdir:
    # the default mode is 0o775 (depends on umask).
    default_mode = 0o775

    # Folder does not exist and is created successfully.
    iop.os.mkdir(path)
    expected_mode = default_mode & ~get_umask()
    verify_directory(path, mode=expected_mode)

    # Folder exists, operation fails.
    with pytest.raises(OSError) as e:
        iop.os.mkdir(path)
    assert e.value.errno == errno.EEXIST
    verify_directory(path, mode=expected_mode)


def test_os_mkdir_non_default_mode(oop_cleanup, tmpdir):
    iop = oop.getProcessPool("test")
    path = str(tmpdir.join("subdir"))
    mode = 0o766

    # Folder does not exist and is created successfully.
    iop.os.mkdir(path, mode=mode)
    expected_mode = mode & ~get_umask()
    verify_directory(path, mode=expected_mode)

    # Folder exists, operation fails.
    with pytest.raises(OSError) as e:
        iop.os.mkdir(path, mode=mode)
    assert e.value.errno == errno.EEXIST
    verify_directory(path, mode=expected_mode)


def test_os_remove_failed_no_such_file(oop_cleanup, tmpdir):
    iop = oop.getProcessPool("test")
    path = str(tmpdir.join("file"))

    # File does not exist, operation should fail.
    with pytest.raises(OSError) as e:
        iop.os.remove(path)
    assert e.value.errno == errno.ENOENT


def test_os_remove(oop_cleanup, tmpdir):
    iop = oop.getProcessPool("test")
    path = str(tmpdir.join("file"))

    open(path, "w").close()

    # File exists, operation should succeed.
    iop.os.remove(path)
    assert not os.path.exists(path)


def test_os_rename_failed_no_such_file(oop_cleanup, tmpdir):
    iop = oop.getProcessPool("test")
    path_src = str(tmpdir.join("file_src"))
    path_dst = str(tmpdir.join("file_dst"))

    # Source & destination do not exist, operation fails.
    with pytest.raises(OSError) as e:
        iop.os.rename(path_src, path_dst)
    assert e.value.errno == errno.ENOENT

    open(path_dst, "w").close()

    # Source does not exist, destination exists, operation fails.
    with pytest.raises(OSError) as e:
        iop.os.rename(path_src, path_dst)
    assert e.value.errno == errno.ENOENT


def test_os_rename(oop_cleanup, tmpdir):
    iop = oop.getProcessPool("test")
    path_src = str(tmpdir.join("file_src"))
    path_dst = str(tmpdir.join("file_dst"))

    open(path_src, "w").close()

    # Source exists, destination does not exist. Source renamed to destination.
    iop.os.rename(path_src, path_dst)
    assert not os.path.exists(path_src)
    assert os.path.exists(path_dst)

    open(path_src, "w").close()

    # Source & destination exist, override the destination.
    iop.os.rename(path_src, path_dst)
    assert not os.path.exists(path_src)
    assert os.path.exists(path_dst)


def test_os_rmdir_failed_no_such_dir(oop_cleanup, tmpdir):
    iop = oop.getProcessPool("test")
    path = str(tmpdir.join("subdir"))

    # Directory does not exist, operation should fail.
    with pytest.raises(OSError) as e:
        iop.os.rmdir(path)
    assert e.value.errno == errno.ENOENT


def test_os_rmdir(oop_cleanup, tmpdir):
    iop = oop.getProcessPool("test")
    path = str(tmpdir.mkdir("subdir"))

    # Directory exists, operation should succeed.
    iop.os.rmdir(path)
    assert not os.path.exists(path)


def test_os_stat_failed_no_such_file(oop_cleanup, tmpdir):
    iop = oop.getProcessPool("test")
    path = str(tmpdir.join("file"))

    # File does not exist, operation should fail.
    with pytest.raises(OSError) as e:
        iop.os.stat(path)
    assert e.value.errno == errno.ENOENT


def test_os_stat(oop_cleanup, tmpdir):
    iop = oop.getProcessPool("test")
    path = str(tmpdir.join("file"))

    open(path, "w").close()

    # File exists, operation should succeed.
    iop_stat = iop.os.stat(path)
    os_stat = os.stat(path)
    check_stat(iop_stat, os_stat)


def test_os_statvfs_failed_no_such_file(oop_cleanup, tmpdir):
    iop = oop.getProcessPool("test")
    path = str(tmpdir.join("file"))

    # File does not exist, operation should fail.
    with pytest.raises(OSError) as e:
        iop.os.statvfs(path)
    assert e.value.errno == errno.ENOENT


def test_os_statvfs(oop_cleanup, tmpdir):
    iop = oop.getProcessPool("test")
    path = str(tmpdir.join("file"))

    open(path, "w").close()

    # File exists, operation should succeed.
    iop_statvfs = iop.os.statvfs(path)
    os_statvfs = os.statvfs(path)
    check_statvfs(iop_statvfs, os_statvfs)


def test_os_unlink_no_such_file(oop_cleanup, tmpdir):
    iop = oop.getProcessPool("test")
    path = str(tmpdir.join("file"))

    # File does not exist, operation should fail.
    with pytest.raises(OSError) as e:
        iop.os.unlink(path)
    assert e.value.errno == errno.ENOENT


def test_os_unlink(oop_cleanup, tmpdir):
    iop = oop.getProcessPool("test")
    path = str(tmpdir.join("file"))

    open(path, "w").close()

    # File exists, operation should succeed.
    iop.os.unlink(path)
    assert not os.path.exists(path)


# os.path APIs

def test_os_path_isdir(oop_cleanup, tmpdir):
    iop = oop.getProcessPool("test")

    path_dir = str(tmpdir.mkdir("subdir"))
    assert iop.os.path.isdir(path_dir)

    path_file = str(tmpdir.join("file"))
    open(path_file, "w").close()
    assert not iop.os.path.isdir(path_file)

    path_no_such_dir = str(tmpdir.join("no such directory"))
    assert not iop.os.path.isdir(path_no_such_dir)


def test_os_path_islink(oop_cleanup, tmpdir):
    iop = oop.getProcessPool("test")
    link = str(tmpdir.join("link"))

    os.symlink("/no/such/file", link)
    assert iop.os.path.islink(link)


def test_os_path_islink_not_link(oop_cleanup, tmpdir):
    iop = oop.getProcessPool("test")

    # Link doesn't exist.
    link = str(tmpdir.join("link"))
    assert not iop.os.path.islink(link)

    # File is not a link.
    path_file = str(tmpdir.join("file"))
    open(path_file, "w").close()
    assert not iop.os.path.islink(path_file)

    # Directory is not a link.
    path_dir = str(tmpdir)
    assert not iop.os.path.islink(path_dir)


def test_os_path_lexists(oop_cleanup, tmpdir):
    iop = oop.getProcessPool("test")
    link = str(tmpdir.join("link"))

    assert not iop.os.path.lexists(link)

    os.symlink("/no/such/file", link)
    assert iop.os.path.lexists(link)


def test_os_path_exists(oop_cleanup, tmpdir):
    iop = oop.getProcessPool("test")
    path = str(tmpdir.join("file"))

    assert not iop.os.path.exists(path)

    open(path, "w").close()
    assert iop.os.path.exists(path)


# utils APIs

def test_utils_forcelink_failed_no_such_file(oop_cleanup, tmpdir):
    iop = oop.getProcessPool("test")
    path_src = str(tmpdir.join("file"))
    path_dst = path_src + ".link"

    # Source does not exist, operation fails.
    with pytest.raises(OSError) as e:
        iop.utils.forceLink(path_src, path_dst)
    assert e.value.errno == errno.ENOENT


def test_utils_forcelink(oop_cleanup, tmpdir):
    iop = oop.getProcessPool("test")
    path_src1 = str(tmpdir.join("file1"))
    path_src2 = str(tmpdir.join("file2"))
    path_dst = str(tmpdir.join("file.link"))

    open(path_src1, "w").close()
    open(path_src2, "w").close()

    # Link does not exist, created to point to "file1".
    iop.utils.forceLink(path_src1, path_dst)
    assert os.path.samefile(path_src1, path_dst)

    # Link exists, overridden to point to "file2".
    iop.utils.forceLink(path_src2, path_dst)
    assert os.path.samefile(path_src2, path_dst)


def test_utils_rmfile(oop_cleanup, tmpdir):
    iop = oop.getProcessPool("test")

    path = str(tmpdir.join("file"))
    open(path, "w").close()

    # File exists, "rmFile" should succeed.
    assert os.path.exists(path)
    iop.utils.rmFile(path)
    assert not os.path.exists(path)

    # File does not exist, "rmFile" should fail silently.
    iop.utils.rmFile(path)


# glob APIs

def test_glob(oop_cleanup, tmpdir):
    iop = oop.getProcessPool("test")
    path = str(tmpdir)

    all_files = set()
    for i in range(5):
        filename = "file{}".format(i)
        f = tmpdir.join(filename)
        f.write("")
        all_files.add(str(f))

    assert set(iop.glob.glob(path + "/*")) == all_files
    assert set(iop.glob.glob(path + "/file[0-4]")) == all_files
    assert iop.glob.glob(path + "/file[5-9]") == []
    assert iop.glob.glob(path + "/file[4-9]") == [str(tmpdir.join("file4"))]


# External APIs

def test_read_lines(oop_cleanup, user_mount):
    iop = oop.getProcessPool("test")
    path = os.path.join(user_mount, "file")

    with open(path, "wb") as f:
        f.write(b"1\n2\n3\n")
    assert iop.readLines(path) == [b"1", b"2", b"3"]


def test_write_lines(oop_cleanup, user_mount):
    iop = oop.getProcessPool("test")
    path = os.path.join(user_mount, "file")

    iop.writeLines(path, [b"1\n", b"2\n", b"3\n"])
    with open(path, "rb") as f:
        assert f.read() == b"1\n2\n3\n"


def test_write_file_direct_false(oop_cleanup, user_mount):
    iop = oop.getProcessPool("test")
    path = os.path.join(user_mount, "file")

    iop.writeFile(path, b"1\n2\n3\n", direct=False)
    with open(path, "rb") as f:
        assert f.read() == b"1\n2\n3\n"


def test_write_file_direct_true_aligned(oop_cleanup, user_mount):
    iop = oop.getProcessPool("test")
    path = os.path.join(user_mount, "file")

    iop.writeFile(path, b"x" * 4096, direct=True)
    with open(path, "rb") as f:
        assert f.read() == b"x" * 4096


def test_write_file_direct_true_unaligned(oop_cleanup, user_mount):
    iop = oop.getProcessPool("test")
    path = os.path.join(user_mount, "file")

    with pytest.raises(OSError) as e:
        iop.writeFile(path, b"1\n2\n3\n", direct=True)
    assert e.value.errno == errno.EINVAL


def test_truncate_file_default_mode(oop_cleanup, user_mount):
    iop = oop.getProcessPool("test")
    path = os.path.join(user_mount, "file")

    iop.truncateFile(path, 10)

    # The test describes the current behavior of ioprocess.truncateFile():
    # the default mode is 0o644 (depends on umask).
    expected_mode = 0o644 & ~get_umask()
    verify_file(path, mode=expected_mode, size=10, content=b"\0" * 10)


@pytest.mark.parametrize("mode", [0o644, 0o700, 0o777])
def test_truncate_file_non_default_mode(oop_cleanup, tmpdir, mode):
    iop = oop.getProcessPool("test")
    path = str(tmpdir.join("file"))

    iop.truncateFile(path, 10, mode)
    verify_file(path, mode, size=10, content=b"\0" * 10)


def test_truncate_existing_file_down(oop_cleanup, tmpdir):
    iop = oop.getProcessPool("test")
    path = str(tmpdir.join("file"))
    mode = 0o644

    with open(path, "wb") as f:
        f.write(b"A" * 10)
    iop.truncateFile(path, 5, mode)
    verify_file(path, mode, size=5, content=b"A" * 5)


def test_truncate_existing_file_up(oop_cleanup, tmpdir):
    iop = oop.getProcessPool("test")
    path = str(tmpdir.join("file"))
    mode = 0o777

    with open(path, "wb") as f:
        f.write(b"A" * 5)
    iop.truncateFile(path, 10, mode)
    verify_file(path, mode, size=10, content=b"A" * 5 + b"\0" * 5)


def test_truncate_file_non_default_creatExcl(oop_cleanup, tmpdir):
    iop = oop.getProcessPool("test")
    path = str(tmpdir.join("file"))
    mode = 0o700
    size = 10

    with open(path, "wb") as f:
        f.write(b"A" * size)

    iop.truncateFile(path, size, mode, creatExcl=False)
    verify_file(path, mode, size)

    with pytest.raises(OSError) as e:
        # Expected to fail, so the file and its properties will stay the same.
        iop.truncateFile(path, size - 5, mode, creatExcl=True)
    assert e.value.errno == errno.EEXIST
    verify_file(path, mode, size)


def test_simple_walk(oop_cleanup, tmpdir):
    iop = oop.getProcessPool("test")
    f1 = tmpdir.join("file1")
    f1.write("")

    d1 = tmpdir.mkdir("subdir1")
    f2 = d1.join("file2")
    f2.write("")
    f3 = d1.join("file3")
    f3.write("")

    d2 = tmpdir.mkdir("subdir2")
    f4 = d2.join("file4")
    f4.write("")

    expected_files = {str(f) for f in [f1, f2, f3, f4]}
    discovered_files = set(iop.simpleWalk(str(tmpdir)))
    assert discovered_files == expected_files


def verify_file(path, mode=None, size=None, content=None):
    assert os.path.isfile(path)

    if size is not None:
        assert os.stat(path).st_size == size

    if mode is not None:
        actual_mode = stat.S_IMODE(os.stat(path).st_mode)
        assert oct(actual_mode) == oct(mode)

    if content is not None:
        with open(path, "rb") as f:
            assert f.read() == content


def verify_directory(path, mode=None):
    assert os.path.isdir(path)

    if mode is not None:
        actual_mode = stat.S_IMODE(os.stat(path).st_mode)
        assert oct(actual_mode) == oct(mode)


@contextmanager
def chown(path, uid=-1, gid=-1):
    """
    Changes path owner (must run as root).

    Change the owner and group id of path to the numeric uid and gid before
    entering the context, and restore the original value when exiting from
    the context.
    To leave one of the ids unchanged, set it to -1.

    Arguments:
        path (str): file path
        uid (int): new Owner ID
        gid (int): new Group ID
    """

    orig_uid = os.stat(path).st_uid
    orig_gid = os.stat(path).st_gid

    os.chown(path, uid, gid)
    try:
        yield
    finally:
        try:
            os.chown(path, orig_uid, orig_gid)
        except Exception as e:
            logging.error("Failed to restore %r to uid %d gid %d: %s",
                          path, orig_uid, orig_gid, e)


def check_stat(iop_stat, os_stat):
    # TODO; similar problem as described in the below "TODO".
    #  "st_blocks" doesn't appear in "os.stat(path)", but its "hasattr"
    #  returns True as if the attribute does appear!
    #  Comparison doesn't fail since both "getattr" return zero.
    common_fields = [field
                     for field in iop_stat._fields
                     if hasattr(os_stat, field)]
    for field in common_fields:
        if field in ("st_atime", "st_mtime", "st_ctime"):
            # These are float\double values and due to the many conversions
            # the values experience during marshaling they cannot be equated.
            # The rest of the fields are a good enough test.
            continue
        assert getattr(iop_stat, field) == getattr(os_stat, field)


def check_statvfs(iop_statvfs, os_statvfs):
    # TODO: need to understand why it doesn't work for "f_fsid".
    #  For some reason "os.statvfs(path)" returns an object without "f_fsid",
    #  but "hasattr" returns True as if the attribute does appear!
    #  After that "getattr" returns some value that fails the below comparison.
    common_fields = [field
                     for field in iop_statvfs._fields
                     if hasattr(os_statvfs, field) and field != "f_fsid"]

    for field in common_fields:
        assert getattr(iop_statvfs, field) == getattr(os_statvfs, field)
