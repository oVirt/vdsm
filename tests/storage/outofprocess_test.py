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
import stat
import time
import weakref

from contextlib import contextmanager

import pytest

from vdsm.storage import outOfProcess as oop
from vdsm.storage.exception import MiscDirCleanupFailure

from . marks import requires_root, requires_unprivileged_user


@pytest.fixture
def oop_cleanup():
    yield
    oop.stop()


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


def test_fileutils_createdir(oop_cleanup, tmpdir):
    iop = oop.getProcessPool("test")
    d1 = str(tmpdir.join("subdir1"))
    d2 = str(tmpdir.join("subdir2"))

    # The test describes the current behavior of ioprocess.fileUtils.createdir:
    # the default mode is 0o775 (depends on umask).
    iop.fileUtils.createdir(d1)
    verify_directory(d1, mode=0o775 & ~get_umask())

    iop.fileUtils.createdir(d2, mode=0o666)
    verify_directory(d2, mode=0o666 & ~get_umask())


@pytest.mark.parametrize("orig_size, expected_size", [
    (4096 - 1, 4096),
    (4096, 4096),
    (4096 + 1, 4096 + 4096),
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

def test_os_path_islink(oop_cleanup, tmpdir):
    iop = oop.getProcessPool("test")
    link = str(tmpdir.join("link"))
    os.symlink("/no/such/file", link)
    assert iop.os.path.islink(link)


def test_os_path_islink_not_link(oop_cleanup, tmpdir):
    iop = oop.getProcessPool("test")
    assert not iop.os.path.islink(str(tmpdir))


def test_os_path_exists(oop_cleanup):
    path = "/dev/null"
    iop = oop.getProcessPool("test")
    assert iop.os.path.exists(path)


# utils APIs

def test_utils_rmfile(oop_cleanup, tmpdir):
    iop = oop.getProcessPool("test")

    path = str(tmpdir.join("file"))
    open(path, "w").close()

    assert os.path.exists(path)
    iop.utils.rmFile(path)
    assert not os.path.exists(path)


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

def test_read_lines(oop_cleanup, tmpdir):
    iop = oop.getProcessPool("test")
    path = str(tmpdir.join("file"))

    with open(path, "wb") as f:
        f.write(b"1\n2\n3\n")
    assert iop.readLines(path) == [b"1", b"2", b"3"]


def test_write_lines(oop_cleanup, tmpdir):
    iop = oop.getProcessPool("test")
    path = str(tmpdir.join("file"))

    iop.writeLines(path, [b"1\n", b"2\n", b"3\n"])
    with open(path, "rb") as f:
        assert f.read() == b"1\n2\n3\n"


def test_write_file_direct_false(oop_cleanup, tmpdir):
    iop = oop.getProcessPool("test")
    path = str(tmpdir.join("file"))

    iop.writeFile(path, b"1\n2\n3\n", direct=False)
    with open(path, "rb") as f:
        assert f.read() == b"1\n2\n3\n"


def test_write_file_direct_true_aligned(oop_cleanup, tmpdir):
    iop = oop.getProcessPool("test")
    path = str(tmpdir.join("file"))

    iop.writeFile(path, b"x" * 4096, direct=True)
    with open(path, "rb") as f:
        assert f.read() == b"x" * 4096


def test_write_file_direct_true_unaligned(oop_cleanup, tmpdir):
    iop = oop.getProcessPool("test")
    path = str(tmpdir.join("file"))

    with pytest.raises(OSError) as e:
        iop.writeFile(path, b"1\n2\n3\n", direct=True)
    assert e.value.errno == errno.EINVAL


def test_truncate_file_default_mode(oop_cleanup, tmpdir):
    iop = oop.getProcessPool("test")
    path = str(tmpdir.join("file"))

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


def get_umask():
    current_umask = os.umask(0)
    os.umask(current_umask)
    return current_umask


@contextmanager
def chmod(path, mode):
    """
    Changes path permissions.

    Change the permissions of path to the numeric mode before entering the
    context, and restore the original value when exiting from the context.

    Arguments:
        path (str): file/directory path
        mode (int): new mode
    """

    orig_mode = stat.S_IMODE(os.stat(path).st_mode)

    os.chmod(path, mode)
    try:
        yield
    finally:
        try:
            os.chmod(path, orig_mode)
        except Exception as e:
            logging.error("Failed to restore %r mode: %s", path, e)


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
