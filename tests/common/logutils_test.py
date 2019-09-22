#
# Copyright 2016-2018 Red Hat, Inc.
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

from __future__ import print_function

import logging
import threading
import time

from contextlib import closing
from contextlib import contextmanager

from testlib import VdsmTestCase as TestCaseBase
from testlib import expandPermutations, permutations
from testlib import forked

from vdsm.common import concurrent
from vdsm.common import logutils


class TestAllVmStats(TestCaseBase):

    _STATS = [{'foo': 'bar',
               'status': 'Up',
               'vmId': u'43f02a2d-e563-4f11-a7bc-9ee191cfeba1'},
              {'foo': 'bar',
               'status': 'Powering up',
               'vmId': u'bd0d066b-971e-42f8-8bc6-d647ab7e0e70'}]
    _SIMPLIFIED = ({u'43f02a2d-e563-4f11-a7bc-9ee191cfeba1': 'Up',
                    u'bd0d066b-971e-42f8-8bc6-d647ab7e0e70': 'Powering up'})

    def test_allvmstats(self):
        data = logutils.AllVmStatsValue(self._STATS)
        result = str(data)
        self.assertEqual(eval(result), self._SIMPLIFIED)


class TestSetLevel(TestCaseBase):

    @forked
    def test_root_logger(self):
        logger = logging.getLogger()
        logutils.set_level("WARNING")
        self.assertEqual(logger.getEffectiveLevel(), logging.WARNING)

    @forked
    def test_other_logger(self):
        name = "test"
        logger = logging.getLogger(name)
        logutils.set_level("WARNING", name=name)
        self.assertEqual(logger.getEffectiveLevel(), logging.WARNING)

    @forked
    def test_sub_logger(self):
        name = "test.sublogger"
        logger = logging.getLogger(name)
        logutils.set_level("WARNING", name=name)
        self.assertEqual(logger.getEffectiveLevel(), logging.WARNING)

    @forked
    def test_non_existing_level(self):
        with self.assertRaises(ValueError):
            logutils.set_level("NO SUCH LEVEL")

    @forked
    def test_level_alias(self):
        logging.addLevelName("OOPS", logging.ERROR)
        logger = logging.getLogger()

        # The new alias should work...
        logutils.set_level("OOPS")
        self.assertEqual(logger.getEffectiveLevel(), logging.ERROR)

        # The old name should work as well.
        logutils.set_level("ERROR")
        self.assertEqual(logger.getEffectiveLevel(), logging.ERROR)


@contextmanager
def threaded_handler(capacity, target, adaptive=True):
    # Start the handler explicitly for deterministic capacity handling.
    handler = logutils.ThreadedHandler(
        capacity, adaptive=adaptive, start=False)
    with closing(handler):
        handler.setTarget(target)
        logger = logging.Logger("test")
        logger.addHandler(handler)
        yield handler, logger


class Handler(object):
    """
    A handler for testing composite handlers such as ThreadedHandler.
    """

    def __init__(self, delay=0):
        self.lock = threading.Lock()
        self.level = logging.DEBUG
        self.messages = []
        self.delay = delay
        self.buffering = False

    def handle(self, record):
        with self.lock:
            msg = record.msg % record.args
            self.messages.append(msg)
            self.flush()

    def flush(self):
        """
        This triggers the actual write() syscall, possibly blocking on
        slow file system.
        """
        if self.buffering:
            return
        if self.delay:
            now = time.time()
            deadline = now + self.delay
            while now < deadline:
                time.sleep(deadline - now)
                now = time.time()


@expandPermutations
class TestThreadedHandler(TestCaseBase):

    # Notes:
    # - When using adaptive log level, we start to drop debug messages when
    #   queue is 60% full, so we want to check critical messages, which are
    #   dropped only when the queue is 100% full.

    @permutations([
        # adaptive, level
        (False, logging.DEBUG),
        (True, logging.CRITICAL),
    ])
    def test_capacity(self, adaptive, level):
        target = Handler()

        with threaded_handler(
                100, target, adaptive=adaptive) as (handler, logger):
            for _ in range(100):
                logger.log(level, "It works!")
            handler.start()

        # We expect that no message will be dropped.
        self.assertEqual(target.messages, ["It works!"] * 100)

    @permutations([
        # adaptive, level
        (False, logging.DEBUG),
        (True, logging.CRITICAL),
    ])
    def test_drop_new_messages(self, adaptive, level):
        target = Handler()

        # This handler will queue up to 10 messages. Logging 20 messages
        # will drop the newest 10 messages.
        with threaded_handler(
                10, target, adaptive=adaptive) as (handler, logger):
            for i in range(20):
                logger.log(level, "Message %d", i)
            handler.start()

        expected = ["Message %d" % i for i in range(10)]
        self.assertEqual(target.messages, expected)

    def test_level_debug(self):
        target = Handler()
        with threaded_handler(10, target) as (handler, logger):
            handler.start()
            handler.setLevel(logging.DEBUG)
            logger.debug("Should be logged")

        self.assertEqual(target.messages, ["Should be logged"])

    def test_level_info(self):
        target = Handler()
        with threaded_handler(10, target) as (handler, logger):
            handler.start()
            handler.setLevel(logging.INFO)
            logger.debug("Should not be logged")

        self.assertEqual(target.messages, [])

    def test_adaptive_log_level(self):
        target = Handler()

        with threaded_handler(100, target) as (handler, logger):
            # The first 60 debug messages will be logged.
            for i in range(70):
                logger.debug("debug %d", i)
            # The first 10 info messages will be logged.
            for i in range(20):
                logger.info("info %d", i)
            # The first 10 warning messages will be logged.
            for i in range(20):
                logger.warning("warning %d", i)
            # The first 10 errors messages will be logged.
            for i in range(20):
                logger.error("error %d", i)
            # The first 10 critical messages will be logged.
            for i in range(20):
                logger.critical("critical %d", i)
            # At this point the queue is full - any message will be dropped.
            for i in range(10):
                logger.critical("will be dropped")
            handler.start()

        debug_messages = ["debug %d" % i for i in range(60)]
        self.assertEqual(target.messages[:60], debug_messages)

        info_messages = ["info %d" % i for i in range(10)]
        self.assertEqual(target.messages[60:70], info_messages)

        warning_messages = ["warning %d" % i for i in range(10)]
        self.assertEqual(target.messages[70:80], warning_messages)

        error_messages = ["error %d" % i for i in range(10)]
        self.assertEqual(target.messages[80:90], error_messages)

        critical_messages = ["critical %d" % i for i in range(10)]
        self.assertEqual(target.messages[90:100], critical_messages)

        self.assertEqual(target.messages[100:], [])

    @permutations([
        # adaptive, level
        (False, logging.DEBUG),
        (True, logging.CRITICAL),
    ])
    def test_blocked_handler(self, adaptive, level):
        # Simulate a handler blocked on storage.
        target = Handler()
        target.lock.acquire()
        with threaded_handler(
                100, target, adaptive=adaptive) as (handler, logger):
            handler.start()
            for _ in range(100):
                logger.log(level, "It works!")

            # Nothing was logged yet, since handler is blocked...
            self.assertEqual(target.messages, [])
            target.lock.release()

        # We expect that no message will be dropped.
        self.assertEqual(target.messages, ["It works!"] * 100)

    @permutations([
        # adaptive, level
        (False, logging.DEBUG),
        (True, logging.CRITICAL),
    ])
    def test_slow_handler(self, adaptive, level):
        # Test that logging threads are not delayed by a slow handler.
        target = Handler(0.1)

        with threaded_handler(
                10, target, adaptive=adaptive) as (handler, logger):
            handler.start()

            def worker(n):
                start = time.time()
                logger.log(level, "thread %02d", n)
                return time.time() - start

            results = concurrent.tmap(worker, iter(range(10)))
            workers_time = [r.value for r in results]

        # All messages should be logged.
        self.assertEqual(len(target.messages), 10)

        # No thread should be delayed.
        # Here is typical (sorted) result:
        # [0.000039, 0.000071, 0.000076, 0.000086, 0.000112, 0.000191,
        #  0.000276, 0.000285, 0.000413, 0.000590]
        print("workers_time %s" % workers_time)
        self.assertLess(max(workers_time), 0.1)

    @permutations([
        # adaptive, level
        (False, logging.DEBUG),
        (True, logging.CRITICAL),
    ])
    def test_deferred_flushing(self, adaptive, level):
        # Time logging of 1000 messages with slow handler. This should take at
        # least 10 seconds with standard logging handlers, but only fraction of
        # the time with deferred flushing.
        target = Handler(0.01)

        with threaded_handler(
                1000, target, adaptive=adaptive) as (handler, logger):
            handler.start()

            def worker(n):
                for i in range(100):
                    logger.log(level, "thread %02d:%03d", n, i)

            start = time.time()
            list(concurrent.tmap(worker, iter(range(10))))

        elapsed = time.time() - start

        # All messages should be logged.
        self.assertEqual(len(target.messages), 1000)

        # This takes 0.09 seconds on my laptop. Use more time to avoid random
        # failures on overloaded slave.
        self.assertLess(elapsed, 1.0)

        print("Logged %d messages in %.2f seconds" % (
              len(target.messages), elapsed))
