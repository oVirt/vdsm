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

from __future__ import print_function
import collections
import contextlib
import copy
import errno
import fcntl
import gc
import logging
import os
import os.path
import select
import signal
import tempfile
import threading
import time
import timeit

from vdsm import taskset
from vdsm import utils
from vdsm import cmdutils
from vdsm import commands
from vdsm import panic
from vdsm.common import zombiereaper

from monkeypatch import MonkeyPatch, MonkeyPatchScope
from vmTestsData import VM_STATUS_DUMP
from monkeypatch import Patch
from testlib import forked, online_cpus, namedTemporaryDir
from testlib import permutations, expandPermutations
from testlib import VdsmTestCase as TestCaseBase
from testValidation import brokentest
from multiprocessing import Process

EXT_SLEEP = "sleep"


class TestException(Exception):
    pass


class FakeMonotonicTime(object):

    def __init__(self, now):
        self.now = now
        self.patch = Patch([
            (utils, 'monotonic_time', self.monotonic_time),
            (time, 'sleep', self.sleep),
        ])

    def monotonic_time(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds

    def __enter__(self):
        self.patch.apply()
        return self

    def __exit__(self, *_):
        self.patch.revert()


def wait_for_removal(path, timeout, wait=0.1):
    deadline = utils.monotonic_time() + timeout
    while True:
        if not os.path.exists(path):
            return True
        if utils.monotonic_time() > deadline:
            return False
        time.sleep(wait)


class TerminatingTests(TestCaseBase):

    def setUp(self):
        self.proc = commands.execCmd([EXT_SLEEP, "2"], sync=False)
        self.proc_path = "/proc/%d" % self.proc.pid
        self.kill_proc = self.proc.kill
        self.assertTrue(os.path.exists(self.proc_path))
        self.reaped = set()

    def tearDown(self):
        if self.proc.poll() is None:
            self.kill_proc()

    def test_terminating(self):
        with utils.terminating(self.proc):
            self.assertTrue(os.path.exists(self.proc_path))
        self.assertTrue(wait_for_removal(self.proc_path, timeout=1))

    def test_terminating_with_kill_exception(self):
        class FakeKillError(Exception):
            pass

        def fake_kill():
            raise FakeKillError("fake kill exception")

        with MonkeyPatchScope([(zombiereaper,
                                'autoReapPID',
                                self.reaped.add
                                )]):
            self.proc.kill = fake_kill
            with utils.terminating(self.proc):
                self.assertTrue(os.path.exists(self.proc_path))
            self.assertTrue(self.proc.pid not in self.reaped)

    def test_terminating_with_infected_kill(self):
        with MonkeyPatchScope([(zombiereaper,
                                'autoReapPID',
                                self.reaped.add
                                )]):
            self.proc.kill = lambda: None
            with utils.terminating(self.proc):
                self.assertTrue(os.path.exists(self.proc_path))
            self.assertTrue(self.proc.pid in self.reaped)


class RetryTests(TestCaseBase):
    def testStopCallback(self):
        counter = [0]
        limit = 4

        def stopCallback():
            counter[0] += 1
            if counter[0] == limit:
                return True

            return False

        def foo():
            raise RuntimeError("If at first you don't succeed, try, try again."
                               "Then quit. There's no point in being a damn"
                               "fool about it.")
            # W. C. Fields

        self.assertRaises(RuntimeError, utils.retry, foo, tries=(limit + 10),
                          sleep=0, stopCallback=stopCallback)
        # Make sure we had the proper amount of iterations before failing
        self.assertEquals(counter[0], limit)

    @brokentest("deadline is not respected")
    def testTimeoutDeadlineReached(self):
        # time  action
        # 0     first attempt
        # 1     sleep
        # 2     second attempt
        # 3     bail out (3 == deadline)
        with FakeMonotonicTime(0):

            def operation():
                time.sleep(1)
                raise RuntimeError

            self.assertRaises(RuntimeError, utils.retry, operation,
                              timeout=3, sleep=1)
            self.assertEqual(utils.monotonic_time(), 3)

    @brokentest("sleep is not considered in deadline calculation")
    def testTimeoutNoTimeForSleep(self):
        # time  action
        # 0     first attempt
        # 1     bail out (1 + 1 == deadline)
        with FakeMonotonicTime(0):

            def operation():
                time.sleep(1)
                raise RuntimeError

            self.assertRaises(RuntimeError, utils.retry, operation,
                              timeout=2, sleep=1)
            self.assertEqual(utils.monotonic_time(), 1)

    def testTimeoutSleepOnce(self):
        # time  action
        # 0     first attempt
        # 2     sleep
        # 3     second attempt
        # 5     bail out (5 > deadline)
        with FakeMonotonicTime(0):
            counter = [0]

            def operation():
                time.sleep(2)
                counter[0] += 1
                raise RuntimeError

            self.assertRaises(RuntimeError, utils.retry, operation,
                              timeout=4, sleep=1)
            self.assertEqual(counter[0], 2)
            self.assertEqual(utils.monotonic_time(), 5)

    def testTimeoutZero(self):
        counter = [0]

        def operation():
            counter[0] += 1
            raise RuntimeError

        tries = 10
        self.assertRaises(RuntimeError, utils.retry, operation,
                          tries=tries, timeout=0.0, sleep=0.0)
        self.assertEqual(counter[0], tries)


class PidStatTests(TestCaseBase):

    @MonkeyPatch(cmdutils, "_USING_CPU_AFFINITY", False)
    def test_without_affinity(self):
        args = ["sleep", "3"]
        sproc = commands.execCmd(args, sync=False)
        stats = utils.pidStat(sproc.pid)
        pid = int(stats.pid)
        # procName comes in the format of (procname)
        name = stats.comm
        self.assertEquals(pid, sproc.pid)
        self.assertEquals(name, args[0])
        sproc.kill()
        sproc.wait()


class PgrepTests(TestCaseBase):
    def test(self):
        sleepProcs = []
        for i in range(3):
            sleepProcs.append(commands.execCmd([EXT_SLEEP, "3"], sync=False,
                              sudo=False))

        pids = utils.pgrep(EXT_SLEEP)
        for proc in sleepProcs:
            self.assertTrue(proc.pid in pids, "pid %d was not located by pgrep"
                            % proc.pid)

        for proc in sleepProcs:
            proc.kill()
            proc.wait()


class GetCmdArgsTests(TestCaseBase):
    def test(self):
        args = [EXT_SLEEP, "4"]
        sproc = commands.execCmd(args, sync=False)
        try:
            cmd_args = utils.getCmdArgs(sproc.pid)
            # let's ignore optional taskset at the beginning
            self.assertEquals(cmd_args[-len(args):],
                              tuple(args))
        finally:
            sproc.kill()
            sproc.wait()

    def testZombie(self):
        args = [EXT_SLEEP, "0"]
        sproc = commands.execCmd(args, sync=False)
        sproc.kill()
        try:
            test = lambda: self.assertEquals(utils.getCmdArgs(sproc.pid),
                                             tuple())
            utils.retry(AssertionError, test, tries=10, sleep=0.1)
        finally:
            sproc.wait()


class CommandPathTests(TestCaseBase):
    def testExisting(self):
        cp = utils.CommandPath('sh', 'utter nonsense', '/bin/sh')
        self.assertEquals(cp.cmd, '/bin/sh')

    def testExistingNotInPaths(self):
        """Tests if CommandPath can find the executable like the 'which' unix
        tool"""
        cp = utils.CommandPath('sh', 'utter nonsense')
        _, stdout, _ = commands.execCmd(['which', 'sh'])
        self.assertIn(cp.cmd, stdout)

    def testMissing(self):
        NAME = 'nonsense'
        try:
            utils.CommandPath(NAME, 'utter nonsense').cmd
        except OSError as e:
            self.assertEquals(e.errno, errno.ENOENT)
            self.assertIn(NAME, e.strerror)


@expandPermutations
class GeneralUtilsTests(TestCaseBase):
    def testPanic(self):
        self.assertRaises(AssertionError, panic.panic, "panic test")

    def testAnyFnmatch(self):
        self.assertTrue(utils.anyFnmatch('test1', ['test0', 'test1']))

    def testReadMemInfo(self):
        meminfo = utils.readMemInfo()
        # most common fields as per man 5 proc
        # add your own here
        fields = ('MemTotal', 'MemFree', 'Buffers', 'Cached', 'SwapCached',
                  'Active', 'Inactive', 'SwapTotal', 'SwapFree', 'Dirty',
                  'Writeback', 'Mapped', 'Slab', 'VmallocTotal',
                  'VmallocUsed', 'VmallocChunk')
        for field in fields:
            self.assertIn(field, meminfo)
            self.assertTrue(isinstance(meminfo[field], int))

    def testParseMemInfo(self):
        testPath = os.path.realpath(__file__)
        dirName = os.path.dirname(testPath)
        path = os.path.join(dirName, "mem_info.out")
        with open(path) as f:
            meminfo = utils._parseMemInfo(f.readlines())
        # testing some random fields
        self.assertEquals(meminfo['NFS_Unstable'], 0)
        self.assertEquals(meminfo['KernelStack'], 2760)
        self.assertEquals(meminfo['Inactive'], 1432748)

    @permutations([
        ([], []),
        ((), []),
        ((i for i in [1, 2, 3, 1, 3]), [1, 2, 3]),
        (('a', 'a', 'b', 'c', 'a', 'd'), ['a', 'b', 'c', 'd']),
        (['a', 'a', 'b', 'c', 'a', 'd'], ['a', 'b', 'c', 'd'])
    ])
    def test_unique(self, iterable, unique_items):
        self.assertEquals(utils.unique(iterable,), unique_items)

    def test_rget_key_exists(self):
        self.assertEqual(
            utils.rget({'a': {'b': 'hello'}}, ('a', 'b')),
            'hello')

    def test_rget_key_missing(self):
        self.assertEqual(
            utils.rget({'a': {'b': 'hello'}}, ('a', 'c'), default='bye'),
            'bye')


class AsyncProcessOperationTests(TestCaseBase):
    def _echo(self, text):
        proc = commands.execCmd(["echo", "-n", "test"], sync=False)

        def parse(rc, out, err):
            return out

        return utils.AsyncProcessOperation(proc, parse)

    def _sleep(self, t):
        proc = commands.execCmd(["sleep", str(t)], sync=False)
        return utils.AsyncProcessOperation(proc)

    def _fail(self, t):
        proc = commands.execCmd(["sleep", str(t)], sync=False)

        def parse(rc, out, err):
            raise Exception("TEST!!!")

        return utils.AsyncProcessOperation(proc, parse)

    def test(self):
        aop = self._sleep(1)
        self.assertEquals(aop.result(), ((0, "", ""), None))

    def testAlreadyExitedSuccess(self):
        aop = self._sleep(0)
        time.sleep(1)
        self.assertEquals(aop.result(), ((0, "", ""), None))

    def testAlreadyExitedFail(self):
        aop = self._sleep("hello")
        time.sleep(1)
        ((rc, out, err), err) = aop.result()
        self.assertEquals(err, None)
        self.assertEquals(rc, 1)

    def testWait(self):
        aop = self._sleep(1)
        aop.wait(timeout=2)

    def testParser(self):
        aop = self._echo("test")
        self.assertEquals(aop.result(), ("test", None))

    def testStop(self):
        aop = self._sleep(10)
        aop.stop()

        start = time.time()
        aop.result()
        end = time.time()
        duration = end - start
        self.assertTrue(duration < 2)

    def testException(self):
        aop = self._fail(1)
        res, err = aop.result()
        self.assertEquals(res, None)
        self.assertNotEquals(err, None)


class CallbackChainTests(TestCaseBase):
    def testCanPassIterableOfCallbacks(self):
        f = lambda: False
        callbacks = [f] * 10
        chain = utils.CallbackChain(callbacks)
        self.assertEqual(list(chain.callbacks), callbacks)

    def testEmptyChainIsNoop(self):
        chain = utils.CallbackChain()
        self.assertFalse(chain.callbacks)
        chain.start()
        chain.join()
        # assert exception isn't thrown in start on empty chain

    def testAllCallbacksAreInvokedIfTheyReturnFalse(self):
        n = 10
        counter = [n]

        def callback():
            counter[0] -= 1
            return False

        chain = utils.CallbackChain([callback] * n)
        chain.start()
        chain.join()
        self.assertEqual(counter[0], 0)

    def testChainStopsAfterSuccessfulCallback(self):
        n = 10
        counter = [n]

        def callback():
            counter[0] -= 1
            return counter[0] == 5

        chain = utils.CallbackChain([callback] * n)
        chain.start()
        chain.join()
        self.assertEquals(counter[0], 5)

    def testArgsPassedToCallback(self):
        callbackArgs = ('arg', 42, 'and another')
        callbackKwargs = {'some': 42, 'kwargs': []}

        def callback(*args, **kwargs):
            self.assertEqual(args, callbackArgs)
            self.assertEqual(kwargs, callbackKwargs)

        chain = utils.CallbackChain()
        chain.addCallback(callback, *callbackArgs, **callbackKwargs)
        chain.start()
        chain.join()


@contextlib.contextmanager
def loghandler(handler, logger=""):
    log = logging.getLogger(logger)
    log.addHandler(handler)
    try:
        yield {}
    finally:
        log.removeHandler(handler)


class TracebackTests(TestCaseBase):

    def __init__(self, *a, **kw):
        self.record = None
        super(TestCaseBase, self).__init__(*a, **kw)

    def testDefaults(self):
        @utils.traceback()
        def fail():
            raise Exception
        with loghandler(self):
            self.assertRaises(Exception, fail)
        self.assertEquals(self.record.name, "root")
        self.assertTrue(self.record.exc_text is not None)

    def testOn(self):
        logger = "test"

        @utils.traceback(on=logger)
        def fail():
            raise Exception
        with loghandler(self, logger=logger):
            self.assertRaises(Exception, fail)
        self.assertEquals(self.record.name, logger)

    def testMsg(self):
        @utils.traceback(msg="WAT")
        def fail():
            raise Exception
        with loghandler(self):
            self.assertRaises(Exception, fail)
        self.assertEquals(self.record.message, "WAT")

    # Logging handler interface

    level = logging.DEBUG

    def acquire(self):
        pass

    def release(self):
        pass

    def handle(self, record):
        assert self.record is None
        self.record = record


class RollbackContextTests(TestCaseBase):

    class UndoException(Exception):
        """A special exception for testing exceptions during undo functions"""

    class OriginalException(Exception):
        """A special exception for testing exceptions in the with statement"""

    def setUp(self):
        self._called = 0

    def _callDef(self):
        self._called += 1
        self.log.info("Incremented call count (%d)", self._called)

    def _raiseDef(self, ex=Exception()):
        self.log.info("Raised exception (%s)", ex.__class__.__name__)
        raise ex

    def test(self):
        with utils.RollbackContext() as rollback:
            rollback.prependDefer(self._callDef)

        self.assertEquals(self._called, 1)

    def testRaise(self):
        """
        Test that raising an exception in a deferred action does
        not block all subsequent actions from running
        """
        try:
            with utils.RollbackContext() as rollback:
                rollback.prependDefer(self._callDef)
                rollback.prependDefer(self._raiseDef)
                rollback.prependDefer(self._callDef)
        except Exception:
            self.assertEquals(self._called, 2)
            return

        self.fail("Exception was not raised")

    def testFirstUndoException(self):
        """
        Test that if multiple actions raise an exception only the first one is
        raised. When performing a batch rollback operations, probably the first
        exception is the root cause.
        """
        try:
            with utils.RollbackContext() as rollback:
                rollback.prependDefer(self._callDef)
                rollback.prependDefer(self._raiseDef)
                rollback.prependDefer(self._callDef)
                rollback.prependDefer(self._raiseDef, RuntimeError())
                rollback.prependDefer(self._callDef)
        except RuntimeError:
            self.assertEquals(self._called, 3)
            return
        except Exception:
            self.fail("Wrong exception was raised")

        self.fail("Exception was not raised")

    def testKeyError(self):
        """
        KeyError is raised as a tuple and not expection. Re-raising it
        should be aware of this fact and handled carfully.
        """
        try:
            with utils.RollbackContext():
                {}['aKey']
        except KeyError:
            return
        except Exception:
            self.fail("Wrong exception was raised")

        self.fail("Exception was not raised")

    def testPreferOriginalException(self):
        """
        Test that if an exception is raised both from the with
        statement and from the finally clause, the one from the with
        statement is the one that's actually raised.
        More info in: http://docs.python.org/
        2.6/library/stdtypes.html#contextmanager.__exit__
        """
        try:
            with utils.RollbackContext() as rollback:
                rollback.prependDefer(self._raiseDef, self.UndoException())
                raise self.OriginalException()
        except self.OriginalException:
            return
        except self.UndoException:
            self.fail("Wrong exception was raised - from undo function. \
                        should have re-raised OriginalException")
        except Exception:
            self.fail("Wrong exception was raised")

        self.fail("Exception was not raised")


class ExecCmdAffinityTests(TestCaseBase):

    CPU_SET = frozenset([0])

    @forked
    @MonkeyPatch(cmdutils, '_USING_CPU_AFFINITY', False)
    def testResetAffinityByDefault(self):
        try:
            proc = commands.execCmd((EXT_SLEEP, '30s'), sync=False)

            self.assertEquals(taskset.get(proc.pid),
                              taskset.get(os.getpid()))
        finally:
            proc.kill()

    @forked
    @MonkeyPatch(cmdutils, '_USING_CPU_AFFINITY', True)
    def testResetAffinityWhenConfigured(self):
        taskset.set(os.getpid(), self.CPU_SET)
        self.assertEquals(taskset.get(os.getpid()), self.CPU_SET)

        try:
            proc = commands.execCmd((EXT_SLEEP, '30s'), sync=False)

            self.assertEquals(taskset.get(proc.pid), online_cpus())
        finally:
            proc.kill()

    @forked
    @MonkeyPatch(cmdutils, '_USING_CPU_AFFINITY', True)
    def testKeepAffinity(self):
        taskset.set(os.getpid(), self.CPU_SET)
        self.assertEquals(taskset.get(os.getpid()), self.CPU_SET)

        try:
            proc = commands.execCmd((EXT_SLEEP, '30s'),
                                    sync=False,
                                    resetCpuAffinity=False)

            self.assertEquals(taskset.get(proc.pid), self.CPU_SET)
        finally:
            proc.kill()


class PickleCopyTests(TestCaseBase):
    def test_picklecopy_exact(self):
        self.assertEqual(utils.picklecopy(VM_STATUS_DUMP),
                         copy.deepcopy(VM_STATUS_DUMP))

    def test_picklecopy_faster(self):
        setup = """
import copy
from vdsm import utils
import vmTestsData
"""
        base = timeit.timeit('copy.deepcopy(vmTestsData.VM_STATUS_DUMP)',
                             setup=setup,
                             number=1000)
        hack = timeit.timeit('utils.picklecopy(vmTestsData.VM_STATUS_DUMP)',
                             setup=setup,
                             number=1000)
        # to justify this hack, it needs to be significantly faster, not
        # just a bit faster, hence the divisor
        # assertLess* requires python 2.7
        self.assertTrue(
            hack < base / 2,
            "picklecopy [%f] not faster than deepcopy [%f]" % (hack, base))


@expandPermutations
class MemoizedTests(TestCaseBase):

    def setUp(self):
        self.values = {}
        self.accessed = collections.defaultdict(int)

    @permutations([[()], [("a",)], [("a", "b")]])
    def test_memoized_method(self, args):
        self.values[args] = 42
        self.assertEqual(self.accessed[args], 0)
        self.assertEqual(self.memoized_method(*args), 42)
        self.assertEqual(self.accessed[args], 1)
        self.assertEqual(self.memoized_method(*args), 42)
        self.assertEqual(self.accessed[args], 1)

    @permutations([[()], [("a",)], [("a", "b")]])
    def test_memoized_function(self, args):
        self.values[args] = 42
        self.assertEqual(self.accessed[args], 0)
        self.assertEqual(memoized_function(self, *args), 42)
        self.assertEqual(self.accessed[args], 1)
        self.assertEqual(memoized_function(self, *args), 42)
        self.assertEqual(self.accessed[args], 1)

    def test_key_error(self):
        self.assertRaises(KeyError, self.memoized_method)
        self.assertRaises(KeyError, self.memoized_method, "a")
        self.assertRaises(KeyError, self.memoized_method, "a", "b")

    def test_invalidate_method(self):
        args = ("a",)
        self.values[args] = 42
        self.assertEqual(self.memoized_method(*args), 42)
        self.memoized_method.invalidate()
        self.assertEqual(self.memoized_method(*args), 42)
        self.assertEqual(self.accessed[args], 2)

    def test_invalidate_function(self):
        args = ("a",)
        self.values[args] = 42
        self.assertEqual(memoized_function(self, *args), 42)
        memoized_function.invalidate()
        self.assertEqual(memoized_function(self, *args), 42)
        self.assertEqual(self.accessed[args], 2)

    @utils.memoized
    def memoized_method(self, *args):
        return self.get(args)

    def get(self, key):
        self.accessed[key] += 1
        return self.values[key]


@utils.memoized
def memoized_function(test, *args):
    return test.get(args)


@expandPermutations
class RoundTests(TestCaseBase):

    @permutations([
        # n, size, result
        (0, 1024, 0),
        (1, 1024, 1024),
        (3.14, 1024, 1024),
        (1024, 1024, 1024),
        (1025, 1024, 2048),
    ])
    def test_round(self, n, size, result):
        self.assertEqual(utils.round(n, size), result)


class FakeLogger(object):

    def __init__(self, level):
        self.level = level
        self.messages = []

    def debug(self, fmt, *args):
        # Will fail bad logging call with mismatched fmt and args
        self.messages.append(fmt % args)

    def isEnabledFor(self, level):
        return self.level <= level


class StopwatchTests(TestCaseBase):

    def test_notset(self):
        log = FakeLogger(logging.NOTSET)
        with utils.stopwatch("message", log=log):
            pass
        self.assertNotEqual(log.messages, [])

    def test_debug(self):
        log = FakeLogger(logging.DEBUG)
        with utils.stopwatch("message", log=log):
            pass
        self.assertNotEqual(log.messages, [])

    def test_info(self):
        log = FakeLogger(logging.INFO)
        with utils.stopwatch("message", log=log):
            pass
        self.assertEqual(log.messages, [])


class ObjectWithDel(object):

    def public(self, *args, **kw):
        return 'public', args, kw

    def __del__(self):
        print('__del__', self.__class__.__name__)


class TestWeakmethod(TestCaseBase):

    def setUp(self):
        self.saved_flags = gc.get_debug()
        gc.disable()
        gc.set_debug(0)

    def tearDown(self):
        gc.collect()
        for obj in gc.garbage:
            if type(obj) is ObjectWithDel:
                obj.public = None
                gc.garbage.remove(obj)
        gc.set_debug(self.saved_flags)
        gc.enable()

    def test_with_reference_cycle(self):
        def _leaking_wrapper(meth):
            def wrapper(*args, **kwargs):
                return meth(*args, **kwargs)
            return wrapper

        obj = ObjectWithDel()
        obj.public = _leaking_wrapper(obj.public)
        self.assertEquals(obj.public(), ("public", (), {}))
        del obj
        gc.collect()
        self.assertIn(ObjectWithDel, [type(obj) for obj in gc.garbage])

    def test_without_reference_cycle(self):
        obj = ObjectWithDel()
        obj.public = utils.weakmethod(obj.public)
        self.assertEquals(obj.public(), ("public", (), {}))
        del obj
        gc.collect()
        self.assertNotIn(ObjectWithDel, [type(obj) for obj in gc.garbage])

    def test_raise_on_invalid_weakref(self):
        obj = ObjectWithDel()
        method = utils.weakmethod(obj.public)
        obj.public = method
        self.assertEquals(obj.public(), ("public", (), {}))
        del obj
        self.assertRaises(utils.InvalidatedWeakRef, method)


class NoIntrPollTests(TestCaseBase):
    RETRIES = 3
    SLEEP_INTERVAL = 0.1

    def _waitAndSigchld(self):
        time.sleep(self.SLEEP_INTERVAL)
        os.kill(os.getpid(), signal.SIGCHLD)

    def _startFakeSigchld(self):
        def _repeatFakeSigchld():
            for i in range(self.RETRIES):
                self._waitAndSigchld()
        intrThread = threading.Thread(target=_repeatFakeSigchld)
        intrThread.setDaemon(True)
        intrThread.start()

    def _noIntrWatchFd(self, fd, isEpoll, mask=select.POLLERR):
        if isEpoll:
            poller = select.epoll()
            pollInterval = self.SLEEP_INTERVAL * self.RETRIES * 2
        else:
            poller = select.poll()
            pollInterval = self.SLEEP_INTERVAL * self.RETRIES * 2 * 1000

        poller.register(fd, mask)
        utils.NoIntrPoll(poller.poll, pollInterval)
        poller.unregister(fd)

    def testWatchFile(self):
        tempFd, tempPath = tempfile.mkstemp()
        os.unlink(tempPath)
        self._startFakeSigchld()
        # only poll can support regular file
        self._noIntrWatchFd(tempFd, isEpoll=False)

    def testWatchPipeEpoll(self):
        myPipe, hisPipe = os.pipe()
        self._startFakeSigchld()
        self._noIntrWatchFd(myPipe, isEpoll=True)  # caught IOError

    def testWatchPipePoll(self):
        myPipe, hisPipe = os.pipe()
        self._startFakeSigchld()
        self._noIntrWatchFd(myPipe, isEpoll=False)  # caught select.error

    def testNoTimeoutPipePoll(self):
        def _sigChldAndClose(fd):
            self._waitAndSigchld()
            time.sleep(self.SLEEP_INTERVAL)
            os.close(fd)

        myPipe, hisPipe = os.pipe()

        poller = select.poll()
        poller.register(myPipe, select.POLLHUP)

        intrThread = threading.Thread(target=_sigChldAndClose, args=(hisPipe,))
        intrThread.setDaemon(True)
        intrThread.start()

        try:
            self.assertTrue(len(utils.NoIntrPoll(poller.poll, -1)) > 0)
        finally:
            os.close(myPipe)

    def testClosedPipe(self):
        def _closePipe(pipe):
            time.sleep(self.SLEEP_INTERVAL)
            os.close(pipe)

        myPipe, hisPipe = os.pipe()
        proc = Process(target=_closePipe, args=(hisPipe,))
        proc.start()
        # no exception caught
        self._noIntrWatchFd(myPipe, isEpoll=False, mask=select.POLLIN)
        proc.join()

    def testPipeWriteEAGAIN(self):
        def _raiseEAGAIN(pipe):
            PIPE_BUF_BYTES = 65536
            longStr = '0' * (1 + PIPE_BUF_BYTES)
            for i in range(self.RETRIES):
                time.sleep(self.SLEEP_INTERVAL)
                try:
                    os.write(pipe, longStr)
                except OSError as e:
                    if e.errno not in (errno.EINTR, errno.EAGAIN):
                        raise

        myPipe, hisPipe = os.pipe()
        fcntl.fcntl(hisPipe, fcntl.F_SETFL, os.O_NONBLOCK)
        fcntl.fcntl(myPipe, fcntl.F_SETFL, os.O_NONBLOCK)
        proc = Process(target=_raiseEAGAIN, args=(hisPipe,))
        proc.start()
        self._noIntrWatchFd(myPipe, isEpoll=False, mask=select.POLLIN)
        proc.join()


class AtomicFileWriteTest(TestCaseBase):

    def test_exception(self):
        TEXT = 'foo'
        with namedTemporaryDir() as tmp_dir:
            test_file_path = os.path.join(tmp_dir, 'foo.txt')
            with self.assertRaises(TestException):
                with utils.atomic_file_write(test_file_path, 'w') as f:
                    f.write(TEXT)
                    raise TestException()
            self.assertFalse(os.path.exists(test_file_path))
            # temporary file was removed
            self.assertEqual(len(os.listdir(tmp_dir)), 0)

    def test_create_a_new_file(self):
        TEXT = 'foo'
        with namedTemporaryDir() as tmp_dir:
            test_file_path = os.path.join(tmp_dir, 'foo.txt')
            with utils.atomic_file_write(test_file_path, 'w') as f:
                f.write(TEXT)
                self.assertFalse(os.path.exists(test_file_path))
            self._assert_file_contains(test_file_path, TEXT)
            # temporary file was removed
            self.assertEqual(len(os.listdir(tmp_dir)), 1)

    def test_edit_file(self):
        OLD_TEXT = 'foo'
        NEW_TEXT = 'bar'
        with namedTemporaryDir() as tmp_dir:
            test_file_path = os.path.join(tmp_dir, 'foo.txt')
            with open(test_file_path, 'w') as f:
                f.write(OLD_TEXT)
            with utils.atomic_file_write(test_file_path, 'w') as f:
                f.write(NEW_TEXT)
                self._assert_file_contains(test_file_path, OLD_TEXT)
            self._assert_file_contains(test_file_path, NEW_TEXT)
            # temporary file was removed
            self.assertEqual(len(os.listdir(tmp_dir)), 1)

    def _assert_file_contains(self, path, expected_content):
        with open(path) as f:
            content = f.read()
            self.assertEqual(content, expected_content)
