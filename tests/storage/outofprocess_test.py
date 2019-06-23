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

import pytest

from vdsm.storage import outOfProcess as oop

import gc
import logging
import os
import tempfile
import time
import re
from weakref import ref

from . marks import xfail_python3


@pytest.fixture
def oop_ns():
    try:
        yield oop.getProcessPool("test")
    finally:
        oop.stop()


def test_os_path_islink(oop_ns, tmpdir):
    link = str(tmpdir.join("link"))
    os.symlink("/no/such/file", link)
    assert oop_ns.os.path.islink(link)


def test_os_path_islink_not_link(oop_ns, tmpdir):
    assert not oop_ns.os.path.islink(str(tmpdir))


def test_same_pool_name(oop_ns):
    poolA = "A"
    pids = []
    for pool in (poolA, poolA):
        proc = oop.getProcessPool(pool)._ioproc
        name = proc._commthread.getName()
        pids.append(int(re.search(r'\d+', name).group()))

    assert pids[0] == pids[1]


def test_different_pool_name(oop_ns):
    poolA = "A"
    poolB = "B"
    pools = (poolA, poolB)
    pids = []
    for pool in pools:
        proc = oop.getProcessPool(pool)._ioproc
        name = proc._commthread.name
        pids.append(int(re.search(r'\d+', name).group()))

    assert pids[0] != pids[1]


@xfail_python3
def test_amount_of_instances_per_pool_name(oop_ns, monkeypatch):
    monkeypatch.setattr(oop, 'IOPROC_IDLE_TIME', 0.5)
    poolA = "A"
    poolB = "B"
    wrapper = ref(oop.getProcessPool(poolA))
    ioproc = ref(oop.getProcessPool(poolA)._ioproc)
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


def test_fileutils_call(oop_ns):
    """fileUtils is a custom module and calling it might break even though
    built in module calls aren't broken"""
    path = "/dev/null"
    assert oop_ns.fileUtils.pathExists(path)


def test_sub_module_call(oop_ns):
    path = "/dev/null"
    assert oop_ns.os.path.exists(path)


def test_utils_funcs(oop_ns):
    tmpfd, tmpfile = tempfile.mkstemp()
    oop_ns.utils.rmFile(tmpfile)
    os.close(tmpfd)
    return True
