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
import threading
from time import sleep, time
from testrunner import VdsmTestCase as TestCaseBase

import storage.processPool as processPool
from testValidation import slowtest


def unstoppableTask(timeout):
    startTime = time()
    while (time() - startTime) < timeout:
        try:
            sleep(1)
        except:
            pass


def thrower():
    raise TypeError("The is incorrect and wil never be correct")


def dummy():
    try:
        sleep(100)
    except:
        pass


class ProcessPoolTests(TestCaseBase):
    def setUp(self):
        self.maxHelpers = 10
        self.pool = processPool.ProcessPool(self.maxHelpers, 2, 10)

    def testWorking(self):
        self.assertEquals(self.pool.runExternally(sum, (1, 2, 3, 4)), 10)

    def testErrorResp(self):
        self.assertRaises(TypeError, self.pool.runExternally, sum,
                "NOT.A.NUMBER")

    def testStuckButFreed(self):
        self.pool.timeout = 2
        self.assertRaises(KeyboardInterrupt, self.pool.runExternally, sleep,
                50)

    def testStuck(self):
        self.pool.timeout = 2
        self.assertRaises(Exception, self.pool.runExternally, unstoppableTask,
                50)

    def testManyRequests(self):
        for i in range(9000):
            self.assertEquals(self.pool.runExternally(sum, (1, 2, 3, 4)), 10)

    def testDecorator(self):
        getpid_oop = self.pool.wrapFunction(os.getpid)
        self.assertTrue(getpid_oop() != os.getpid())

    def testDecoratorWithException(self):
        thrower_oop = self.pool.wrapFunction(thrower)
        self.assertRaises(TypeError, thrower_oop)

    def testReuseAfterException(self):
        thrower_oop = self.pool.wrapFunction(thrower)
        self.assertRaises(TypeError, thrower_oop)
        getpid_oop = self.pool.wrapFunction(os.getpid)
        self.assertTrue(getpid_oop() != os.getpid())

    @slowtest
    def testMaxSimultaniousCalls(self):
        threads = []
        # It is possible that 10 seconds will pass
        # before all the threads are running. This will
        # cause the test to fail. If you have a better idea
        # please change this.
        self.pool.timeout = 10
        exceptCounter = [0]

        def threadHandler():
            try:
                self.pool.runExternally(dummy)
            except processPool.NoFreeHelpersError:
                exceptCounter[0] += 1

        for i in range(self.maxHelpers + 1):
                threads.append(threading.Thread(target=threadHandler))
                threads[-1].start()

        for thread in threads:
            thread.join()

        self.assertEquals(exceptCounter[0], 1)

    @slowtest
    def testClose(self):
        self.log.info("Running a command to create a helper")
        self.pool.runExternally(sum, (1, 2, 3, 4))
        self.log.info("Collecting PIDs")
        procs = []
        for helper in self.pool._helperPool:
            if helper is not None:
                procs.append(helper.proc)
        self.log.info("Closing pool")
        self.pool.close()
        self.log.info("Waiting for children to die")
        self.log.info("Making sure they are dead")
        for proc in procs:
            try:
                proc.join(30)
            except OSError:
                pass
            self.assertRaises(OSError, os.kill, proc.pid, 0)

    def tearDown(self):
        self.pool.close()
