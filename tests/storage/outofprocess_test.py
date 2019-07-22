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
import time
import weakref
import stat

import pytest

from vdsm.storage import outOfProcess as oop


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


def test_fileutils_pathexists(oop_cleanup):
    iop = oop.getProcessPool("test")
    path = "/dev/null"
    assert iop.fileUtils.pathExists(path)


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
        # Expected to fail with "OSError: [Errno 22] Invalid argument".
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
        # Expected to fail with "OSError: [Errno 17] File exists",
        # so the file and it's properties should stay the same.
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
    assert os.path.exists(path)

    if size is not None:
        assert os.stat(path).st_size == size

    if mode is not None:
        actual_mode = stat.S_IMODE(os.stat(path).st_mode)
        assert oct(actual_mode) == oct(mode)

    if content is not None:
        with open(path, "rb") as f:
            assert f.read() == content


def get_umask():
    current_umask = os.umask(0)
    os.umask(current_umask)
    return current_umask
