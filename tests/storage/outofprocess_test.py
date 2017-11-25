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

from vdsm.storage import outOfProcess as oop

from testlib import VdsmTestCase
from monkeypatch import MonkeyPatchScope

import gc
import logging
import os
import tempfile
import time
import re
from weakref import ref


class TestOopWrapper(VdsmTestCase):

    def setUp(self):
        self.pool = oop.getGlobalProcPool()

    def tearDown(self):
        oop.stop()

    def testSamePoolName(self):
        poolA = "A"
        pids = []
        for pool in (poolA, poolA):
            proc = oop.getProcessPool(pool)._ioproc
            name = proc._commthread.getName()
            pids.append(int(re.search(r'\d+', name).group()))

        self.assertEqual(pids[0], pids[1])

    def testDifferentPoolName(self):
        poolA = "A"
        poolB = "B"
        pools = (poolA, poolB)
        pids = []
        for pool in pools:
            proc = oop.getProcessPool(pool)._ioproc
            name = proc._commthread.name
            pids.append(int(re.search(r'\d+', name).group()))

        self.assertNotEquals(pids[0], pids[1])

    def testAmountOfInstancesPerPoolName(self):
        with MonkeyPatchScope([(oop, 'IOPROC_IDLE_TIME', 0.5)]):
            poolA = "A"
            poolB = "B"
            wrapper = ref(oop.getProcessPool(poolA))
            ioproc = ref(oop.getProcessPool(poolA)._ioproc)
            oop.getProcessPool(poolA)
            time.sleep(oop.IOPROC_IDLE_TIME + 0.5)
            oop.getProcessPool(poolB)
            self.assertEqual(wrapper(), None)
            gc.collect()
            try:
                self.assertEqual(ioproc(), None)
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

        self.assertEqual(self.pool._ioproc.echo(data), data)

    def testFileUtilsCall(self):
        """fileUtils is a custom module and calling it might break even though
        built in module calls arn't broken"""
        path = "/dev/null"
        self.assertEqual(self.pool.fileUtils.pathExists(path), True)

    def testSubModuleCall(self):
        path = "/dev/null"
        self.assertEqual(self.pool.os.path.exists(path), True)

    def testUtilsFuncs(self):
        tmpfd, tmpfile = tempfile.mkstemp()
        self.pool.utils.rmFile(tmpfile)
        os.close(tmpfd)
        return True
