#
# Copyright 2012-2017 Red Hat, Inc.
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
import copy
import cpopen
import gc
import logging
import operator
import os
import os.path
import signal
import sys
import time
import timeit

from vdsm import taskset
from vdsm import utils
from vdsm import cmdutils
from vdsm import commands
from vdsm.common import logutils
import vdsm.common.time

from monkeypatch import MonkeyPatch
from vmTestsData import VM_STATUS_DUMP
from monkeypatch import Patch
from fakelib import FakeLogger
from testlib import forked, online_cpus
from testlib import permutations, expandPermutations
from testlib import VdsmTestCase as TestCaseBase
from testValidation import brokentest

EXT_SLEEP = "sleep"


class FakeMonotonicTime(object):

    def __init__(self, now):
        self.now = now
        self.patch = Patch([
            (vdsm.common.time, 'monotonic_time', self.monotonic_time),
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


class TestTerminating(TestCaseBase):

    def setUp(self):
        self.proc = commands.execCmd([EXT_SLEEP, "2"], sync=False)
        self.proc_poll = self.proc.poll
        self.proc_kill = self.proc.kill
        self.proc_wait = self.proc.wait

    def tearDown(self):
        if self.proc_poll() is None:
            self.proc_kill()
            self.proc_wait()

    def test_process_running(self):
        with utils.terminating(self.proc):
            self.assertIsNone(self.proc_poll())
        self.assertEqual(self.proc.returncode, -signal.SIGKILL)

    def test_process_zombie(self):
        self.proc.terminate()
        wait_for_zombie(self.proc, 1)

        def fail():
            raise RuntimeError("Attempt to kill a zombie process")

        self.proc.kill = fail
        with utils.terminating(self.proc):
            pass
        self.assertEqual(self.proc.returncode, -signal.SIGTERM)

    def test_process_terminated(self):
        self.proc.terminate()
        self.proc.wait()

        def fail():
            raise RuntimeError("Attempt to kill a terminated process")

        self.proc.kill = fail
        with utils.terminating(self.proc):
            pass
        self.assertEqual(self.proc.returncode, -signal.SIGTERM)

    def test_poll_failure(self):
        def fail():
            raise ExpectedFailure("Fake poll failure")

        self.proc.poll = fail
        self.check_failure()

    def test_kill_failure(self):
        def fail():
            raise ExpectedFailure("Fake kill failure")

        self.proc.kill = fail
        self.check_failure()

    def test_wait_failure(self):
        def fail():
            raise ExpectedFailure("Fake wait failure")

        self.proc.wait = fail
        self.check_failure()

    def check_failure(self):
        with self.assertRaises(utils.TerminatingFailure) as e:
            with utils.terminating(self.proc):
                self.assertIsNone(self.proc_poll())

        self.assertEqual(e.exception.pid, self.proc.pid)
        self.assertEqual(type(e.exception.error), ExpectedFailure)

        # Note: We cannot check return code since AsyncProc.returncode is a
        # property calling poll(). The return code here may be None or -9,
        # depeending on timing.


class ExpectedFailure(Exception):
    pass


def wait_for_zombie(proc, timeout, interval=0.1):
    interval = min(interval, timeout)
    deadline = vdsm.common.time.monotonic_time() + timeout
    while True:
        time.sleep(interval)
        if is_zombie(proc):
            return
        if vdsm.common.time.monotonic_time() > deadline:
            raise RuntimeError("Timeout waiting for process")


def is_zombie(proc):
    proc_stat = "/proc/%d/stat" % proc.pid
    with open(proc_stat) as f:
        line = f.readline()
    state = line.split()[2]
    return state == "Z"


class TestRetry(TestCaseBase):
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
        self.assertEqual(counter[0], limit)

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
            self.assertEqual(vdsm.common.time.monotonic_time(), 3)

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
            self.assertEqual(vdsm.common.time.monotonic_time(), 1)

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
            self.assertEqual(vdsm.common.time.monotonic_time(), 5)

    def testTimeoutZero(self):
        counter = [0]

        def operation():
            counter[0] += 1
            raise RuntimeError

        tries = 10
        self.assertRaises(RuntimeError, utils.retry, operation,
                          tries=tries, timeout=0.0, sleep=0.0)
        self.assertEqual(counter[0], tries)


class TestGetCmdArgs(TestCaseBase):
    def test(self):
        args = [EXT_SLEEP, "4"]
        sproc = commands.execCmd(args, sync=False)
        try:
            cmd_args = utils.getCmdArgs(sproc.pid)
            # let's ignore optional taskset at the beginning
            self.assertEqual(cmd_args[-len(args):],
                             tuple(args))
        finally:
            sproc.kill()
            sproc.wait()

    def testZombie(self):
        args = [EXT_SLEEP, "0"]
        sproc = commands.execCmd(args, sync=False)
        sproc.kill()
        try:
            test = lambda: self.assertEqual(utils.getCmdArgs(sproc.pid),
                                            tuple())
            utils.retry(AssertionError, test, tries=10, sleep=0.1)
        finally:
            sproc.wait()


@expandPermutations
class TestGeneralUtils(TestCaseBase):

    def test_panic(self):
        cmd = [sys.executable, "panic_helper.py"]
        rc, out, err = commands.execCmd(cmd)
        self.assertEqual(rc, -9)

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
        self.assertEqual(meminfo['NFS_Unstable'], 0)
        self.assertEqual(meminfo['KernelStack'], 2760)
        self.assertEqual(meminfo['Inactive'], 1432748)

    @permutations([
        ([], []),
        ((), []),
        ((i for i in [1, 2, 3, 1, 3]), [1, 2, 3]),
        (('a', 'a', 'b', 'c', 'a', 'd'), ['a', 'b', 'c', 'd']),
        (['a', 'a', 'b', 'c', 'a', 'd'], ['a', 'b', 'c', 'd'])
    ])
    def test_unique(self, iterable, unique_items):
        self.assertEqual(utils.unique(iterable,), unique_items)


class TestAsyncProcessOperation(TestCaseBase):
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
        self.assertEqual(aop.result(), ((0, "", ""), None))

    def testAlreadyExitedSuccess(self):
        aop = self._sleep(0)
        time.sleep(1)
        self.assertEqual(aop.result(), ((0, "", ""), None))

    def testAlreadyExitedFail(self):
        aop = self._sleep("hello")
        time.sleep(1)
        ((rc, out, err), err) = aop.result()
        self.assertEqual(err, None)
        self.assertEqual(rc, 1)

    def testWait(self):
        aop = self._sleep(1)
        aop.wait(timeout=2)

    def testParser(self):
        aop = self._echo("test")
        self.assertEqual(aop.result(), ("test", None))

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
        self.assertEqual(res, None)
        self.assertNotEquals(err, None)


class TestCallbackChain(TestCaseBase):
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
        self.assertEqual(counter[0], 5)

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


class TestTraceback(TestCaseBase):

    def test_failure(self):
        log = FakeLogger()

        @logutils.traceback(log=log, msg="message")
        def fail():
            raise Exception

        self.assertRaises(Exception, fail)
        self.assertEqual(log.messages,
                         [(logging.ERROR, "message", {"exc_info": True})])


class TestRollbackContext(TestCaseBase):

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

        self.assertEqual(self._called, 1)

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
            self.assertEqual(self._called, 2)
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
            self.assertEqual(self._called, 3)
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


class TestExecCmdAffinity(TestCaseBase):

    CPU_SET = frozenset([0])

    @forked
    @MonkeyPatch(cmdutils, '_USING_CPU_AFFINITY', False)
    def testResetAffinityByDefault(self):
        try:
            proc = commands.execCmd((EXT_SLEEP, '30s'), sync=False)

            self.assertEqual(taskset.get(proc.pid),
                             taskset.get(os.getpid()))
        finally:
            proc.kill()

    @forked
    @MonkeyPatch(cmdutils, '_USING_CPU_AFFINITY', True)
    def testResetAffinityWhenConfigured(self):
        taskset.set(os.getpid(), self.CPU_SET)
        self.assertEqual(taskset.get(os.getpid()), self.CPU_SET)

        try:
            proc = commands.execCmd((EXT_SLEEP, '30s'), sync=False)

            self.assertEqual(taskset.get(proc.pid), online_cpus())
        finally:
            proc.kill()

    @forked
    @MonkeyPatch(cmdutils, '_USING_CPU_AFFINITY', True)
    def testKeepAffinity(self):
        taskset.set(os.getpid(), self.CPU_SET)
        self.assertEqual(taskset.get(os.getpid()), self.CPU_SET)

        try:
            proc = commands.execCmd((EXT_SLEEP, '30s'),
                                    sync=False,
                                    resetCpuAffinity=False)

            self.assertEqual(taskset.get(proc.pid), self.CPU_SET)
        finally:
            proc.kill()


class TestPickleCopy(TestCaseBase):
    def test_picklecopy_exact(self):
        self.assertEqual(utils.picklecopy(VM_STATUS_DUMP),
                         copy.deepcopy(VM_STATUS_DUMP))

    def test_picklecopy_faster(self):
        setup = """
import copy
from vdsm import utils
from vmTestsData import VM_STATUS_DUMP
"""
        deepcopy = timeit.timeit('copy.deepcopy(VM_STATUS_DUMP)',
                                 setup=setup,
                                 number=1000)
        picklecopy = timeit.timeit('utils.picklecopy(VM_STATUS_DUMP)',
                                   setup=setup,
                                   number=1000)
        print("deepcopy: %.3f, picklecopy: %.3f"
              % (deepcopy, picklecopy), end=" ")
        self.assertLess(picklecopy, deepcopy)


class UserError(Exception):
    """ A special excpetion for testing errors during object life """


class CloseError(Exception):
    """ A special exception for testing errors during closing an object """


class Closer:
    def __init__(self):
        self.was_closed = False

    def close(self):
        self.was_closed = True
        raise CloseError


class TestClosing(TestCaseBase):
    def test_error_before_close(self):
        c = Closer()
        with self.assertRaises(UserError):
            with utils.closing(c):
                raise UserError
            self.assertTrue(c.was_closed)

    def test_error_while_closing(self):
        c = Closer()
        with self.assertRaises(CloseError):
            with utils.closing(c):
                pass


@expandPermutations
class TestRound(TestCaseBase):

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


@expandPermutations
class TestCommandStream(TestCaseBase):

    def assertUnexpectedCall(self, data):
        raise AssertionError("Unexpected data: %r" % data)

    def _startCommand(self, command):
        return cpopen.CPopen(command)

    @permutations([
        (['echo', '-n', '%s'], True, False),
        (['sh', '-c', 'echo -n "%s" >&2'], False, True),
    ])
    def test_receive(self, cmd, recv_out, recv_err):
        text = bytes('Hello World')
        received = bytearray()

        def recv_data(buffer):
            # cannot use received += buffer with a variable
            # defined in the parent function.
            operator.iadd(received, buffer)

        cmd[-1] = cmd[-1] % text

        c = self._startCommand(cmd)
        p = utils.CommandStream(
            c,
            recv_data if recv_out else self.assertUnexpectedCall,
            recv_data if recv_err else self.assertUnexpectedCall)
        with utils.closing(p):
            while not p.closed:
                p.receive()

        retcode = c.wait()

        self.assertEqual(retcode, 0)
        self.assertEqual(text, received)

    @permutations([
        (['cat'], True, False),
        (['sh', '-c', 'cat >&2'], False, True),
    ])
    def test_write(self, cmd, recv_out, recv_err):
        text = bytes('Hello World')
        received = bytearray()

        def recv_data(buffer):
            # cannot use received += buffer with a variable
            # defined in the parent function.
            operator.iadd(received, buffer)

        c = self._startCommand(cmd)
        p = utils.CommandStream(
            c,
            recv_data if recv_out else self.assertUnexpectedCall,
            recv_data if recv_err else self.assertUnexpectedCall)
        with utils.closing(p):
            c.stdin.write(text)
            c.stdin.flush()
            c.stdin.close()

            while not p.closed:
                p.receive()

        retcode = c.wait()

        self.assertEqual(retcode, 0)
        self.assertEqual(text, str(received))

    def test_timeout(self):
        c = self._startCommand(["sleep", "5"])
        p = utils.CommandStream(c, self.assertUnexpectedCall,
                                self.assertUnexpectedCall)
        with utils.closing(p):
            with self.assertElapsed(2):
                p.receive(2)

            self.assertEqual(p.closed, False)

        c.terminate()

        self.assertEqual(c.wait(), -signal.SIGTERM)

    @permutations((
        ('kill', -signal.SIGKILL),
        ('terminate', -signal.SIGTERM),
    ))
    def test_signals(self, method, expected_retcode):
        c = self._startCommand(["sleep", "2"])
        p = utils.CommandStream(c, self.assertUnexpectedCall,
                                self.assertUnexpectedCall)
        with utils.closing(p):
            getattr(c, method)()

            try:
                with self.assertElapsed(0):
                    p.receive(2)
            finally:
                retcode = c.wait()

        self.assertEqual(retcode, expected_retcode)


@expandPermutations
class TestStopwatch(TestCaseBase):

    @permutations([(logging.NOTSET,), (logging.DEBUG,)])
    def test_default_level_log(self, level):
        log = FakeLogger(level)
        with utils.stopwatch("message", log=log):
            time.sleep(0.01)
        self.assertNotEqual(log.messages, [])
        level, message, kwargs = log.messages[0]
        print("Logged: %s" % message, end=" ")
        self.assertEqual(level, logging.DEBUG)
        self.assertTrue(message.startswith("message"),
                        "Unexpected message: %s" % message)

    def test_default_level_no_log(self):
        log = FakeLogger(logging.INFO)
        with utils.stopwatch("message", log=log):
            pass
        self.assertEqual(log.messages, [])

    def test_custom_level_log(self):
        log = FakeLogger(logging.INFO)
        with utils.stopwatch("message", level=logging.INFO, log=log):
            pass
        self.assertNotEqual(log.messages, [])


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
        self.assertEqual(obj.public(), ("public", (), {}))
        del obj
        gc.collect()
        self.assertIn(ObjectWithDel, [type(obj) for obj in gc.garbage])

    def test_without_reference_cycle(self):
        obj = ObjectWithDel()
        obj.public = utils.weakmethod(obj.public)
        self.assertEqual(obj.public(), ("public", (), {}))
        del obj
        gc.collect()
        self.assertNotIn(ObjectWithDel, [type(obj) for obj in gc.garbage])

    def test_raise_on_invalid_weakref(self):
        obj = ObjectWithDel()
        method = utils.weakmethod(obj.public)
        obj.public = method
        self.assertEqual(obj.public(), ("public", (), {}))
        del obj
        self.assertRaises(utils.InvalidatedWeakRef, method)
