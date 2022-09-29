# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

import errno
import os
import stat
import sys

import selinux
import pytest

from vdsm.common.osutils import get_umask
from vdsm.storage import exception as se
from vdsm.storage import fileUtils

from testlib import namedTemporaryDir
from testlib import temporaryPath

from . marks import (
    requires_root,
    requires_selinux,
    requires_unprivileged_user,
)

# createdir tests


def test_createdir_no_mode():
    with namedTemporaryDir() as base:
        path = os.path.join(base, "a", "b")
        assert not os.path.isdir(path)
        fileUtils.createdir(path)
        assert os.path.isdir(path)


def test_createdir_with_mode():
    with namedTemporaryDir() as base:
        path = os.path.join(base, "a", "b")
        mode = 0o700
        fileUtils.createdir(path, mode=mode)
        assert os.path.isdir(path)
        actual_mode = stat.S_IMODE(os.lstat(path).st_mode)
        assert oct(actual_mode) == oct(mode)


def test_createdir_with_mode_intermediate():
    with namedTemporaryDir() as base:
        intermediate_path = os.path.join(base, "a")
        leaf_path = os.path.join(intermediate_path, "b")
        leaf_mode = 0o700

        fileUtils.createdir(leaf_path, mode=leaf_mode)
        assert os.path.isdir(leaf_path)
        actual_mode = stat.S_IMODE(os.lstat(intermediate_path).st_mode)

        # TODO: remove when all platforms support python-3.7
        if sys.version_info[:2] == (3, 6):
            assert oct(actual_mode) == oct(leaf_mode)
        else:
            # os.makedirs() behavior changed since python 3.7,
            # os.makedirs() will not respect the 'mode' parameter for
            # intermediate directories and will create them with the
            # default mode=0o777
            assert oct(actual_mode) == oct(0o777 & ~get_umask())


@requires_unprivileged_user
def test_createdir_raise_errors():
    with namedTemporaryDir() as base:
        path = os.path.join(base, "a", "b")
        mode = 0o400
        # TODO: remove when all platforms support python-3.7
        if sys.version_info[:2] == (3, 6):
            with pytest.raises(OSError) as e:
                fileUtils.createdir(path, mode=mode)
            assert e.value.errno == errno.EACCES
        else:
            # os.makedirs() behavior changed since python 3.7,
            # os.makedirs() will not respect the 'mode' parameter for
            # intermediate directories and will create them with the
            # default mode=0o777
            fileUtils.createdir(path, mode=mode)
            assert os.path.isdir(path)

            actual_mode = stat.S_IMODE(os.lstat(path).st_mode)
            assert oct(actual_mode) == oct(mode & ~get_umask())


def test_createdir_directory_exists_no_mode():
    with namedTemporaryDir() as base:
        fileUtils.createdir(base)


def test_createdir_directory_exists_other_mode():
    with namedTemporaryDir() as base:
        with pytest.raises(OSError) as e:
            fileUtils.createdir(base, mode=0o755)
        assert e.value.errno == errno.EPERM


def test_createdir_directory_exists_same_mode():
    with namedTemporaryDir() as tmpdir:
        path = os.path.join(tmpdir, "subdir")
        mode = 0o766

        os.mkdir(path, mode=mode)

        # Folder exists, mode matches, operation should do nothing.
        fileUtils.createdir(path, mode=mode)
        assert os.path.isdir(path)

        expected_mode = mode & ~get_umask()
        actual_mode = stat.S_IMODE(os.lstat(path).st_mode)
        assert oct(actual_mode) == oct(expected_mode)


def test_createdir_file_exists_with_mode():
    with namedTemporaryDir() as base:
        path = os.path.join(base, "file")
        with open(path, "w"):
            mode = stat.S_IMODE(os.lstat(path).st_mode)
            with pytest.raises(OSError) as e:
                fileUtils.createdir(path, mode=mode)
            assert e.value.errno == errno.ENOTDIR


def test_createdir_file_exists_no_mode():
    with namedTemporaryDir() as base:
        path = os.path.join(base, "file")
        with open(path, "w"):
            with pytest.raises(OSError) as e:
                fileUtils.createdir(path)
            assert e.value.errno == errno.ENOTDIR

# chown tests


@requires_root
@pytest.mark.root
def test_chown():
    targetId = 666
    with temporaryPath() as srcPath:
        fileUtils.chown(srcPath, targetId, targetId)
        stat = os.stat(srcPath)
        assert stat.st_uid == stat.st_gid == targetId


@requires_root
@pytest.mark.root
def test_chown_names():
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


# backup file tests


def test_backup_file_missing(tmpdir):
    path = str(tmpdir.join("orig"))

    backup = fileUtils.backup_file(path)

    assert backup is None
    assert tmpdir.listdir() == []


def test_backup_file_create(tmpdir):
    path = str(tmpdir.join("orig"))
    with open(path, "w") as f:
        f.write("old")

    backup = fileUtils.backup_file(path)

    assert backup.startswith(path + ".")
    with open(backup) as f:
        assert f.read() == "old"


# atomic write tests


def test_atomic_write_create(tmpdir):
    path = str(tmpdir.join("file"))
    fileUtils.atomic_write(path, b"new")

    with open(path, "rb") as f:
        assert f.read() == b"new"
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o644


@pytest.mark.parametrize("mode", [0o700, 0o777])
def test_atomic_write_replace(tmpdir, mode):
    path = str(tmpdir.join("file"))

    # Create old file with differnt mode.
    fileUtils.atomic_write(path, b"old")
    os.chmod(path, mode)

    fileUtils.atomic_write(path, b"new")

    with open(path, "rb") as f:
        assert f.read() == b"new"
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o644


@pytest.mark.parametrize("mode", [0o600, 0o640])
def test_atomic_write_mode(tmpdir, mode):
    path = str(tmpdir.join("file"))

    # Create old file with default mode.
    fileUtils.atomic_write(path, b"old")

    fileUtils.atomic_write(path, b"new", mode=mode)

    with open(path, "rb") as f:
        assert f.read() == b"new"
    assert stat.S_IMODE(os.stat(path).st_mode) == mode


@requires_selinux
def test_atomic_write_relabel(tmpdir):
    path = str(tmpdir.join("file"))

    # Create old file with non-default label.
    fileUtils.atomic_write(path, b"old")
    selinux.setfilecon(path, "unconfined_u:object_r:etc_t:s0")

    fileUtils.atomic_write(path, b"new", relabel=True)

    with open(path, "rb") as f:
        assert f.read() == b"new"
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o644

    # The label depends on the enviroment and selinux policy:
    # - Locally we get: "unconfined_u:object_r:user_tmp_t:s0"
    # - In mock we get: "unconfined_u:object_r:mock_var_lib_t:s0"
    # So lets check what selinux.restorecon(path, force=True) is creating.
    control = str(tmpdir.ensure("control"))
    selinux.restorecon(control, force=True)

    assert selinux.getfilecon(path)[1] == selinux.getfilecon(control)[1]


# atomic symlink tests


def test_atomic_symlink_create_new():
    with namedTemporaryDir() as tmpdir:
        target = os.path.join(tmpdir, "target")
        link = os.path.join(tmpdir, "link")
        fileUtils.atomic_symlink(target, link)
        assert os.readlink(link) == target
        assert not os.path.exists(link + ".tmp")


def test_atomic_symlink_keep_current():
    with namedTemporaryDir() as tmpdir:
        target = os.path.join(tmpdir, "target")
        link = os.path.join(tmpdir, "link")
        fileUtils.atomic_symlink(target, link)
        fileUtils.atomic_symlink(target, link)
        assert os.readlink(link) == target
        assert not os.path.exists(link + ".tmp")


def test_atomic_symlink_replace_stale():
    with namedTemporaryDir() as tmpdir:
        target = os.path.join(tmpdir, "target")
        link = os.path.join(tmpdir, "link")
        fileUtils.atomic_symlink("stale", link)
        fileUtils.atomic_symlink(target, link)
        assert os.readlink(link) == target
        assert not os.path.exists(link + ".tmp")


def test_atomic_symlink_replace_stale_temporary():
    with namedTemporaryDir() as tmpdir:
        target = os.path.join(tmpdir, "target")
        link = os.path.join(tmpdir, "link")
        tmp_link = link + ".tmp"
        fileUtils.atomic_symlink("stale", tmp_link)
        fileUtils.atomic_symlink(target, link)
        assert os.readlink(link) == target
        assert not os.path.exists(tmp_link)


def test_atomic_symlink_error_isfile():
    with namedTemporaryDir() as tmpdir:
        target = os.path.join(tmpdir, "target")
        link = os.path.join(tmpdir, "link")
        with open(link, 'w') as f:
            f.write('data')
        with pytest.raises(OSError) as e:
            fileUtils.atomic_symlink(target, link)
        assert e.value.errno == errno.EINVAL


def test_atomic_symlink_error_isdir():
    with namedTemporaryDir() as tmpdir:
        target = os.path.join(tmpdir, "target")
        link = os.path.join(tmpdir, "link")
        os.mkdir(link)
        with pytest.raises(OSError) as e:
            fileUtils.atomic_symlink(target, link)
        assert e.value.errno == errno.EINVAL

# normalize path tests


@pytest.mark.parametrize("path, normalized_path", [
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
def test_normalize_path_equals(path, normalized_path):
    assert normalized_path == fileUtils.normalize_path(path)


# tarCopy tests


def test_tarcopy(tmpdir):
    src = tmpdir.mkdir("src")
    dst = tmpdir.mkdir("dst")

    # Create simple tree with some data.
    src_file = src.join("file")
    src_file.write("file data")
    src_dir = src.mkdir("dir")
    src_sub_file = src_dir.join("file")
    src_sub_file.write("sub file data")

    fileUtils.tarCopy(str(src), str(dst))

    assert dst.join("file").read() == "file data"
    assert dst.join("dir", "file").read() == "sub file data"


def test_tarcopy_excludes(tmpdir):
    src = tmpdir.mkdir("src")
    dst = tmpdir.mkdir("dst")

    # Create simple tree with some data.
    src_file = src.join("file")
    src_file.write("file data")
    src_dir = src.mkdir("dir")
    src_sub_file = src_dir.join("file")
    src_sub_file.write("sub file data")

    # Data in these directories should be excluded.
    src_skip1 = src.mkdir("skip1")
    src_skip1.join("file").write("")
    src_skip2 = src.mkdir("skip2")
    src_skip2.join("file").write("")

    fileUtils.tarCopy(str(src), str(dst), exclude=["./skip1", "./skip2"])

    assert dst.join("file").read() == "file data"
    assert dst.join("dir", "file").read() == "sub file data"
    assert not dst.join("skip1").check()
    assert not dst.join("skip2").check()


def test_tarcopy_writer_error(tmpdir):
    src = str(tmpdir.mkdir("src"))

    # Destiation directory does not exist.
    dst = str(tmpdir.join("dst"))

    with pytest.raises(se.TarCommandError) as e:
        fileUtils.tarCopy(src, dst)

    msg = str(e.value)

    # Reader was succesful, not included in the error.
    assert src not in msg

    # Writer failed, error included.
    assert dst in msg


def test_tarcopy_reader_writer_error(tmpdir):
    # Source directory does not exist.
    src = str(tmpdir.join("src"))

    dst = str(tmpdir.mkdir("dst"))

    with pytest.raises(se.TarCommandError) as e:
        fileUtils.tarCopy(str(src), str(dst))

    msg = str(e.value)

    # Reader failed since src does not exist.
    assert src in msg

    # Writer failed since no data was written by reader.
    assert dst in msg
