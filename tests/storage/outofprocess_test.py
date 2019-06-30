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

import gc
import logging
import os
import re
import tempfile
import time
import weakref

import pytest

from vdsm.storage import outOfProcess as oop
from . marks import xfail_python3


@pytest.fixture
def oop_cleanup():
    yield
    oop.stop()


def test_os_path_islink(oop_cleanup, tmpdir):
    iop = oop.getProcessPool("test")
    link = str(tmpdir.join("link"))
    os.symlink("/no/such/file", link)
    assert iop.os.path.islink(link)


def test_os_path_islink_not_link(oop_cleanup, tmpdir):
    iop = oop.getProcessPool("test")
    assert not iop.os.path.islink(str(tmpdir))


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


@xfail_python3
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


def test_fileutils_call(oop_cleanup):
    """fileUtils is a custom module and calling it might break even though
    built in module calls aren't broken"""
    iop = oop.getProcessPool("test")
    path = "/dev/null"
    assert iop.fileUtils.pathExists(path)


def test_sub_module_call(oop_cleanup):
    path = "/dev/null"
    iop = oop.getProcessPool("test")
    assert iop.os.path.exists(path)


def test_utils_funcs(oop_cleanup):
    # TODO: There are few issues in this test that we need to fix:
    # 1) Use pytest tmpdir to create temporary file instead of tempfile.
    # 2) Fix fd leak if "oop_ns.utils.rmFile()" raises.
    # 3) Remove the redundant return.
    # 4) Test that the file was actually removed -
    #    the current test does not test anything.
    iop = oop.getProcessPool("test")
    tmpfd, tmpfile = tempfile.mkstemp()
    iop.utils.rmFile(tmpfile)
    os.close(tmpfd)
    return True
