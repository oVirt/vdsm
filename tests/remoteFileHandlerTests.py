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
from vdsm import utils

from testrunner import VdsmTestCase as TestCaseBase
import storage.remoteFileHandler as rhandler

HANDLERS_NUM = 10


class RemoteFileHandlerTests(TestCaseBase):
    def setUp(self):
        self.pool = rhandler.RemoteFileHandlerPool(HANDLERS_NUM)

    def testEcho(self):
        data = """Vince: You're about as edgy as a Satsuma
                  Howard: I'm a crazy man. I'm a nutjob. I'm a freakball.
                  You know? I break through all boundaries.
                  If I see a boundary, I eat a boundary.
                  And wash it down with a cup of hot steaming rules. Eh?."""
               # (C) BBC - The Mighty Boosh

        self.assertEquals(self.pool.callCrabRPCFunction(5, "echo", data), data)

    def testTimeout(self):
        sleep = 5
        self.assertRaises(rhandler.Timeout, self.pool.callCrabRPCFunction,
                          0, "sleep", sleep)

    def testRegeneration(self):
        """Makes all the helpers fail and get killed, it does this more than
        the pool size so technically a new helper has to be created to serve
        the requests"""
        for i in range(HANDLERS_NUM * 2):
            self.testTimeout()
            self.testEcho()

    def tearDown(self):
        self.pool.close()


class PoolHandlerTests(TestCaseBase):
    def testStop(self):
        p = rhandler.PoolHandler()
        procPath = os.path.join("/proc", str(p.process.pid))

        # Make sure handler is running
        self.assertTrue(p.proxy.callCrabRPCFunction(4, "os.path.exists",
                                                    procPath))
        p.stop()
        test = lambda: self.assertFalse(os.path.exists(procPath))

        utils.retry(test, AssertionError, timeout=4, sleep=0.1)
