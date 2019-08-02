#
# Copyright 2015-2019 Red Hat, Inc.
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

from collections import defaultdict
import logging
import threading
import time

from vdsm import executor
from vdsm import schedule
from vdsm import throttledlog
from vdsm.common import exception
from vdsm.common.time import monotonic_time
from vdsm.virt import migration
from vdsm.virt import periodic
from vdsm.virt import vmstatus


from monkeypatch import MonkeyPatchScope
from testValidation import slowtest
from testValidation import broken_on_ci
from testlib import make_config
from testlib import expandPermutations, permutations
from testlib import VdsmTestCase as TestCaseBase
import fakelib
import vmfakelib as fake


@expandPermutations
class TimeoutTests(TestCaseBase):

    @permutations([[0], [0.1], [1], [5], [99]])
    def test_timeout_lesser_or_equal(self, interval):
        self.assertTrue(periodic._timeout_from(interval) <= interval)


class _PeriodicBase(TestCaseBase):

    def setUp(self):
        self.tasks = 2
        self.sched = schedule.Scheduler(name="test.Scheduler",
                                        clock=monotonic_time)
        self.sched.start()

        self.exc = executor.Executor(name="test.Executor",
                                     workers_count=1,
                                     max_tasks=100,
                                     scheduler=self.sched)
        self.exc.start()

    def tearDown(self):
        self.exc.stop(wait=True)
        self.exc = None

        self.sched.stop(wait=True)
        self.sched = None


class PeriodicFunctionsTests(_PeriodicBase):

    def test_start_with_invalid_operation(self):
        """
        periodic.start() should swallow any error that
        periodic.Operation.start() may raise, and keep starting
        the other operations after the failed one.
        """
        lock = threading.Lock()
        done = threading.Event()

        def _work():
            with lock:
                self.tasks -= 1
                if not self.tasks:
                    done.set()

        ops = [
            periodic.Operation(_work,
                               period=1.0,
                               scheduler=self.sched,
                               executor=self.exc),

            # will raise periodic.InvalidValue
            periodic.Operation(lambda: None, period=0,
                               scheduler=self.sched,
                               executor=self.exc),

            periodic.Operation(_work,
                               period=1.0,
                               scheduler=self.sched,
                               executor=self.exc),
        ]

        with MonkeyPatchScope([
            (periodic, 'config',
                make_config([('sampling', 'enable', 'false')])),
            (periodic, '_create', lambda cif, sched: ops),
        ]):
            # Don't assume operations are started in order,
            # we just know all of them will be start()ed.
            # See the documentation of periodic.start()
            periodic.start(fake.ClientIF(), self.sched)

        done.wait(0.5)
        self.assertTrue(done.is_set())


@expandPermutations
class PeriodicOperationTests(_PeriodicBase):

    def test_start(self):
        invoked = threading.Event()

        def _work():
            invoked.set()

        op = periodic.Operation(_work, period=1.0,
                                scheduler=self.sched,
                                executor=self.exc)
        op.start()
        invoked.wait(0.5)
        self.assertTrue(invoked.is_set())

    def test_invalid_period(self):
        op = periodic.Operation(lambda: None, period=0,
                                scheduler=self.sched,
                                executor=self.exc)
        self.assertRaises(periodic.InvalidValue, op.start)

    def test_start_twice(self):

        def _work():
            pass

        op = periodic.Operation(_work, period=1.0,
                                scheduler=self.sched,
                                executor=self.exc)
        op.start()
        self.assertRaises(AssertionError, op.start)

    @permutations([
        # exclusive
        [True],
        [False],
    ])
    def test_repeating(self, exclusive):
        PERIOD = 0.1
        TIMES = 3

        invocations = [0, 0]
        invoked = threading.Event()

        def _work():
            invocations[0] += 1
            invocations[1] = monotonic_time()
            if invocations[0] == TIMES:
                invoked.set()

        op = periodic.Operation(_work, period=PERIOD,
                                scheduler=self.sched,
                                executor=self.exc,
                                exclusive=exclusive)
        op.start()
        invoked.wait(PERIOD * TIMES + PERIOD)
        # depending on timing, _work may be triggered one more time.
        # nothing prevents this, although is unlikely.
        # we don't care of this case
        op.stop()
        self.assertTrue(invoked.is_set())
        self.assertTrue(TIMES <= invocations[0] <= TIMES + 1)

    def test_repeating_exclusive_with_pool_exhausted(self):
        PERIOD = 0.1
        TRIES_BEFORE_SUCCESS = 2

        exc = _RecoveringExecutor(tries_before_success=TRIES_BEFORE_SUCCESS)

        attempts = [0]
        done = threading.Event()

        def _work():
            attempts[0] = exc.attempts
            logging.info('_work invoked after %d attempts', attempts[0])
            done.set()

        op = periodic.Operation(_work, period=PERIOD,
                                scheduler=self.sched,
                                executor=exc,
                                exclusive=True)
        op.start()
        timeout = 2  # seconds
        # We intentionally using a timeout much longer than actually needed
        # the timeout should be >= PERIOD * (TRIES_BEFORE_SUCCESS + 1).
        # We use larger value to reduce the chance of false failures
        # on overloaded CI workers.
        self.assertTrue(done.wait(timeout))
        self.assertEqual(attempts[0], TRIES_BEFORE_SUCCESS + 1)
        op.stop()

    @broken_on_ci("Fails occasionally, don't know why",
                  exception=AssertionError)
    def test_repeating_if_raises(self):
        PERIOD = 0.1
        TIMES = 5

        def _work():
            pass

        exc = _FakeExecutor(fail=True, max_attempts=TIMES)
        op = periodic.Operation(_work, period=PERIOD,
                                scheduler=self.sched,
                                executor=exc)
        op.start()
        completed = exc.done.wait(PERIOD * TIMES + PERIOD)
        # depending on timing, _work may be triggered one more time.
        # nothing prevents this, although is unlikely.
        # we don't care of this case
        op.stop()
        self.assertTrue(completed)
        self.assertTrue(TIMES <= exc.attempts <= TIMES + 1)

    def test_stop(self):
        PERIOD = 0.1

        invocations = [0]

        def _work():
            invocations[0] = monotonic_time()

        op = periodic.Operation(_work, period=PERIOD,
                                scheduler=self.sched,
                                executor=self.exc)
        op.start()
        time.sleep(PERIOD * 2)
        # avoid pathological case on which nothing ever runs
        self.assertTrue(invocations[0] > 0)

        op.stop()

        # cooldown. Let's try to avoid scheduler mistakes.
        time.sleep(PERIOD)
        stop = monotonic_time()

        self.assertTrue(stop > invocations[0])

    @slowtest
    def test_repeating_after_block(self):
        PERIOD = 0.1
        TIMES = 5
        BLOCK_AT = 2

        invocations = [0, 0]
        executions = [0, 0]
        done = threading.Event()

        def _work():
            invocations[0] += 1
            invocations[1] = monotonic_time()
            if invocations[0] == BLOCK_AT:
                # must be > (PERIOD * TIMES) ~= forever
                time.sleep(10 * PERIOD * TIMES)
            executions[0] += 1
            executions[1] = monotonic_time()
            if invocations[0] == TIMES:
                done.set()

        op = periodic.Operation(_work, period=PERIOD,
                                scheduler=self.sched,
                                executor=self.exc)
        op.start()
        done.wait(PERIOD * TIMES + PERIOD)
        # depending on timing, _work may be triggered one more time.
        # nothing prevents this, although is unlikely.
        # we don't care of this case
        op.stop()
        self.assertTrue(done.is_set())
        self.assertTrue(executions[1] >= invocations[1])
        self.assertTrue(TIMES <= invocations[0] <= TIMES + 1)
        # one execution never completed
        self.assertEqual(executions[0], invocations[0] - 1)

    @slowtest
    def test_repeating_exclusive_operation(self):
        PERIOD = 0.2

        executions = [0]
        ready = threading.Event()
        done = threading.Event()

        log = logging.getLogger('test')

        def _work():
            n = executions[0]
            executions[0] += 1
            log.info('BEGIN _work() n=%d', n)
            if n == 0:
                # block just the first time
                # we intentionally don't set done
                # to emulate lost worker
                log.info('waiting for readiness...')
                ready.wait()
                log.info('ready!')
            else:
                done.set()
                log.info('done!')
            log.info('END')

        op = periodic.Operation(_work,
                                period=PERIOD,
                                scheduler=self.sched,
                                executor=self.exc,
                                timeout=None,
                                exclusive=True)
        op.start()
        self.assertFalse(done.wait(PERIOD * 4))
        # we just wait "long enough" to make sure we cross at least one
        # timeout threshold.
        ready.set()
        completed = done.wait(PERIOD * 2)  # guard against races
        op.stop()
        self.assertTrue(completed)
        # op.stop() doesn't guarantee the immediate termination, so _work()
        # can run one extra time
        self.assertGreaterEqual(executions[0], 2)

    def test_dump_executor_state_on_resource_exhausted(self):
        PERIOD = 0.1
        MAX_TASKS = 20  # random value

        log = fakelib.FakeLogger()

        exc = executor.Executor(name="test.Executor",
                                # intentional we  just want to clog the queue
                                workers_count=0,
                                max_tasks=MAX_TASKS,
                                scheduler=self.sched,  # unused
                                max_workers=0,
                                log=log)
        exc.start()

        op = periodic.Operation(lambda: None,
                                period=PERIOD,
                                scheduler=self.sched,
                                executor=exc,
                                timeout=None,
                                exclusive=False)
        with MonkeyPatchScope([
            (throttledlog, '_logger', log),
        ]):
            # the first dispatch is done here
            op.start()
            for _ in range(MAX_TASKS - 1):
                op._dispatch()
            # this will trigger the exception, and the dump
            op._dispatch()
        level, message, args = log.messages[-1]
        self.assertTrue(message.startswith('executor state:'))


VM_NUM = 5  # just a number, no special meaning


VM_IDS = [
    [()],
    [((0,))],
    [((0, 2))],
    [((VM_NUM - 1,))],
    [((VM_NUM - 2, VM_NUM - 1))]
]


@expandPermutations
class VmDispatcherTests(TestCaseBase):

    def setUp(self):
        self.cif = fake.ClientIF()

        self._make_fake_vms()

        _Visitor.VMS.clear()

    @permutations(VM_IDS)
    def test_dispatch(self, failed_ids):
        for i in failed_ids:
            with self.cif.vm_container_lock:
                vm_id = _fake_vm_id(i)
                self.cif.vmContainer[vm_id].fail_required = True

        self._check_dispatching(failed_ids)

    @permutations(VM_IDS)
    def test_skip_not_monitorable(self, unmonitorable_ids):
        for i in unmonitorable_ids:
            with self.cif.vm_container_lock:
                vm_id = _fake_vm_id(i)
                self.cif.vmContainer[vm_id].monitorable = False

        self._check_dispatching(unmonitorable_ids)

    def test_dispatch_fails(self):
        """
        make sure that VmDispatcher attempts to dispatch work
        for every registered VMs, and doesn't exit prematurely
        when one dispatch() fails.
        """
        exc = _FakeExecutor(fail=True)

        op = periodic.VmDispatcher(
            self.cif.getVMs, exc, _Nop, 0)

        skipped = op()

        self.assertEqual(set(skipped),
                         set(self.cif.getVMs().keys()))

    def _check_dispatching(self, skip_ids):
        op = periodic.VmDispatcher(
            self.cif.getVMs, _FakeExecutor(), _Visitor, 0)
        # we don't care about executor (hence the simplistic fake)
        op()

        for vm_id in skip_ids:
            self.assertNotIn(_fake_vm_id(vm_id), _Visitor.VMS)

        vms = self.cif.getVMs()

        expected = (
            set(vms.keys()) -
            set(_fake_vm_id(i) for i in skip_ids)
        )
        for vm_id in expected:
            self.assertEqual(_Visitor.VMS.get(vm_id), 1)

    def _make_fake_vms(self):
        for i in range(VM_NUM):
            vm_id = _fake_vm_id(i)
            with self.cif.vm_container_lock:
                self.cif.vmContainer[vm_id] = _FakeVM(
                    vm_id, vm_id)


def _fake_vm_id(i):
    return 'VM-%03i' % i


class _Visitor(periodic._RunnableOnVm):

    VMS = defaultdict(int)

    @property
    def required(self):
        if getattr(self._vm, 'fail_required', False):
            raise ValueError('required failed')
        return super(_Visitor, self).required

    @property
    def runnable(self):
        if getattr(self._vm, 'fail_runnable', False):
            raise ValueError('runnable failed')
        return super(_Visitor, self).runnable

    def _execute(self):
        _Visitor.VMS[self._vm.id] += 1


class _Nop(periodic._RunnableOnVm):

    @property
    def required(self):
        return True

    @property
    def runnable(self):
        return True

    def _execute(self):
        pass


class _RecoveringExecutor(object):

    def __init__(self, tries_before_success=None):
        self._tries_before_success = max(0, tries_before_success)
        self.attempts = 0

    def dispatch(self, func, timeout, discard=True):
        self.attempts += 1
        exhausted = self._tries_before_success > 0
        if exhausted:
            self._tries_before_success -= 1
            raise exception.ResourceExhausted(resource="test", current_tasks=0)
        else:
            func()

    def __repr__(self):
        return "<%s attempts=%d tries=%d at 0x%x>" % (
            self.__class__.__name__,
            self.attempts,
            self._tries_before_success,
            id(self)
        )


class _FakeExecutor(object):

    def __init__(self, fail=False, max_attempts=None):
        self._fail = fail
        self._max_attempts = max_attempts
        self.attempts = 0
        self.done = threading.Event()

    def dispatch(self, func, timeout, discard=True):
        if (self._max_attempts is not None and
           self.attempts == self._max_attempts):
            self.done.set()

        self.attempts += 1

        if self._fail:
            raise exception.ResourceExhausted(resource="test", current_tasks=0)
        else:
            func()


# fake.VM is a quite complex beast. We need only the bare minimum here,
# literally only `id' and `name', so it seems sensible to create this
# new tiny fake locally.
class _FakeVM(object):
    def __init__(self, vmId, vmName):
        self.id = vmId
        self.name = vmName
        self.migrating = False
        self.lastStatus = vmstatus.UP
        self.monitorable = True
        self.post_copy = migration.PostCopyPhase.NONE
        self.disk_devices = []
        self.updated_drives = []

    def isDomainReadyForCommands(self):
        return True

    def isMigrating(self):
        return self.migrating

    def updateNumaInfo(self):
        pass

    def getDiskDevices(self):
        return self.disk_devices

    def updateDriveVolume(self, vmDrive):
        self.updated_drives.append(vmDrive)


class _FakeDrive(object):

    def __init__(self, name, readonly=False):
        self.name = name
        self.readonly = readonly


class PeriodicActionTests(TestCaseBase):

    def test_update_volumes(self):
        ro_drive = _FakeDrive('ro', readonly=True)
        rw_drive = _FakeDrive('rw', readonly=False)
        vm = _FakeVM('123', 'test')
        vm.disk_devices = [ro_drive, rw_drive]
        periodic.UpdateVolumes(vm)._execute()
        self.assertEqual([d.name for d in vm.updated_drives], [rw_drive.name])
