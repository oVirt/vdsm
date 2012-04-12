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
import threading
from time import sleep

from testrunner import VdsmTestCase as TestCaseBase
import betterThreading


def getTweakedEvent():
    # use betterThreading.Event if exposed. Newer Vdsm only has the
    # monkey-patched threading.Event
    try:
        return betterThreading.Event()
    except AttributeError:
        return threading.Event()


class LockTests(TestCaseBase):
    def testAcquire(self):
        lock = betterThreading.Lock()
        self.assertTrue(lock.acquire)

    def testRelease(self):
        lock = betterThreading.Lock()
        lock.acquire()
        lock.release()
        self.assertTrue(lock.acquire(False))

    def testAcquireNonblocking(self):
        lock = betterThreading.Lock()
        lock.acquire()
        self.assertFalse(lock.acquire(False))


class Flag(object):
    def __init__(self):
        self._flag = False

    def __nonzero__(self):
        return self._flag

    def set(self):
        self._flag = True

    def clear(self):
        self._flag = False


class ConditionTests(TestCaseBase):
    def testBaseTest(self, lock=None, timeout=None):
        """
        Base Condition exerciser
        """
        flag = Flag()
        c = betterThreading.Condition(lock)

        def setter(flag):
            sleep(2)
            with c:
                flag.set()
                c.notify()
        threading.Thread(target=setter, args=(flag,)).start()
        with c:
            while not flag:
                self.log.debug("main waits")
                c.wait(timeout)

        self.assertTrue(flag)

    def testNotifyAll(self, lock=None):
        """
        Exercise Condition.notifyAll()
        """
        flag = Flag()
        c = betterThreading.Condition(lock)

        def setter(flag):
            sleep(2)
            with c:
                flag.set()
                c.notifyAll()
        threading.Thread(target=setter, args=(flag,)).start()
        with c:
            while not flag:
                c.wait()

        self.assertTrue(flag)

    def testXWait(self, lock=None):
        """
        Exercise Condition.wait() with 1s timeout that never become true
        """
        self.log.info("Creating Condition object")
        flag = Flag()
        c = betterThreading.Condition(lock)
        tired = 0
        with c:
            while not flag and tired < 5:
                self.log.debug("main waits")
                c.wait(1)
                tired = 5

        self.assertFalse(flag)

    def testNotify(self):
        """
        Exercise Condition.notify()
        """
        self.testBaseTest()

    def testWaitIntegerTimeout(self):
        """
        Exercise Condition.wait() with 1s timeout
        """
        self.testBaseTest(timeout=1)

    def testWaitFloatTimeout(self):
        """
        Exercise Condition.wait() with 0.3s timeout (fraction of a second)
        """
        self.testBaseTest(timeout=0.3)

    def testNotifyWithUserProvidedLock(self):
        """
        Exercise Condition.notify()
        """
        self.testBaseTest(lock=betterThreading.Lock())

    def testWaitIntegerTimeoutWithUserProvidedLock(self):
        """
        Exercise Condition.wait() with 1s timeout
        """
        self.testBaseTest(lock=betterThreading.Lock(), timeout=1)

    def testWaitFloatTimeoutWithUserProvidedLock(self):
        """
        Exercise Condition.wait() with 0.3s timeout (fraction of a second)
        """
        self.testBaseTest(lock=betterThreading.Lock(), timeout=0.3)


class EventTests(TestCaseBase):
    def _test(self, timeout):
        self.log.info("Creating Event object")
        e = getTweakedEvent()

        def setter():
            self.log.info("Setter thread is sleeping")
            sleep(2)
            self.log.info("Setter thread is setting")
            e.set()
            self.log.info("Event object is set (%s) :D", e.is_set())

        self.log.info("Starting setter thread")
        threading.Thread(target=setter).start()
        self.log.info("Waiting for salvation")
        res = e.wait(timeout)
        self.assertTrue(res is not False)

    def testPassWithTimeout(self):
        self._test(5)

    def testPassWithoutTimeout(self):
        self._test(None)

    def testNotPassTimeout(self):
        self.log.info("Creating Event object")
        e = getTweakedEvent()
        self.log.info("Waiting for salvation (That will never come)")
        res = e.wait(0.5)
        self.assertFalse(res)

    def testZeroTimeout(self):
        self.log.info("Creating Event object")
        e = getTweakedEvent()
        self.log.info("Waiting 0 for salvation (That will never come)")
        res = e.wait(0)
        self.assertFalse(res)
