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


class TestOopWrapper():

    def setup_method(self, m):
        self.pool = oop.getGlobalProcPool()

    def teardown_method(self, m):
        oop.stop()

    def testSamePoolName(self):
        poolA = "A"
        pids = []
        for pool in (poolA, poolA):
            proc = oop.getProcessPool(pool)._ioproc
            name = proc._commthread.getName()
            pids.append(int(re.search(r'\d+', name).group()))

        assert pids[0] == pids[1]

    def testDifferentPoolName(self):
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
    def testAmountOfInstancesPerPoolName(self, monkeypatch):
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

    def testEcho(self):
        data = """Censorship always defeats it own purpose, for it creates in
                  the end the kind of society that is incapable of exercising
                  real discretion."""
        # Henry Steele Commager

        assert self.pool._ioproc.echo(data) == data

    def testFileUtilsCall(self):
        """fileUtils is a custom module and calling it might break even though
        built in module calls arn't broken"""
        path = "/dev/null"
        assert self.pool.fileUtils.pathExists(path)

    def testSubModuleCall(self):
        path = "/dev/null"
        assert self.pool.os.path.exists(path)

    def testUtilsFuncs(self):
        tmpfd, tmpfile = tempfile.mkstemp()
        self.pool.utils.rmFile(tmpfile)
        os.close(tmpfd)
        return True
