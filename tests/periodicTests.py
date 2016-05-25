#
# Copyright 2015 Red Hat, Inc.
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

from collections import defaultdict
import threading
import time

import libvirt

from vdsm import executor
from vdsm import schedule
from vdsm.utils import monotonic_time
from vdsm.virt import periodic
from vdsm.virt import vmstatus


from testValidation import slowtest
from testlib import expandPermutations, permutations
from testlib import VdsmTestCase as TestCaseBase
import vmfakelib as fake


@expandPermutations
class TimeoutTests(TestCaseBase):

    @permutations([[0], [0.1], [1], [5], [99]])
    def test_timeout_lesser_or_equal(self, interval):
        self.assertTrue(periodic._timeout_from(interval) <= interval)


class PeriodicOperationTests(TestCaseBase):

    def setUp(self):
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

    def test_start_twice(self):

        def _work():
            pass

        op = periodic.Operation(_work, period=1.0,
                                scheduler=self.sched,
                                executor=self.exc)
        op.start()
        self.assertRaises(AssertionError, op.start)

    def test_repeating(self):
        PERIOD = 0.1
        TIMES = 3

        invokations = [0, 0]
        invoked = threading.Event()

        def _work():
            invokations[0] += 1
            invokations[1] = monotonic_time()
            if invokations[0] == TIMES:
                invoked.set()

        op = periodic.Operation(_work, period=PERIOD,
                                scheduler=self.sched,
                                executor=self.exc)
        op.start()
        invoked.wait(PERIOD * TIMES + PERIOD)
        # depending on timing, _work may be triggered one more time.
        # nothing prevents this, although is unlikely.
        # we don't care of this case
        op.stop()
        self.assertTrue(invoked.is_set())
        self.assertTrue(TIMES <= invokations[0] <= TIMES+1)

    def test_stop(self):
        PERIOD = 0.1

        invokations = [0]

        def _work():
            invokations[0] = monotonic_time()

        op = periodic.Operation(_work, period=PERIOD,
                                scheduler=self.sched,
                                executor=self.exc)
        op.start()
        time.sleep(PERIOD * 2)
        # avoid pathological case on which nothing ever runs
        self.assertTrue(invokations[0] > 0)

        op.stop()

        # cooldown. Let's try to avoid scheduler mistakes.
        time.sleep(PERIOD)
        stop = monotonic_time()

        self.assertTrue(stop > invokations[0])

    @slowtest
    def test_repeating_after_block(self):
        PERIOD = 0.1
        TIMES = 5
        BLOCK_AT = 2

        invokations = [0, 0]
        executions = [0, 0]
        done = threading.Event()

        def _work():
            invokations[0] += 1
            invokations[1] = monotonic_time()
            if invokations[0] == BLOCK_AT:
                # must be > (PERIOD * TIMES) ~= forever
                time.sleep(10 * PERIOD * TIMES)
            executions[0] += 1
            executions[1] = monotonic_time()
            if invokations[0] == TIMES:
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
        self.assertTrue(executions[1] >= invokations[1])
        self.assertTrue(TIMES <= invokations[0] <= TIMES+1)
        # one execution never completed
        self.assertEqual(executions[0], invokations[0]-1)


VM_NUM = 5  # just a number, no special meaning


@expandPermutations
class VmDispatcherTests(TestCaseBase):

    def setUp(self):
        self.cif = fake.ClientIF()

        self._make_fake_vms()

        _Visitor.VMS.clear()

    @permutations([[()],
                   [((0,))],
                   [((0, 2))],
                   [((VM_NUM-1,))],
                   [((VM_NUM-2, VM_NUM-1))]])
    def test_dispatch(self, failed_ids):
        for i in failed_ids:
            with self.cif.vmContainerLock:
                vm_id = _fake_vm_id(i)
                self.cif.vmContainer[vm_id].fail_required = True

        op = periodic.VmDispatcher(
            self.cif.getVMs, _FakeExecutor(), _Visitor, 0)
        # we don't care about executor (hence the simplistic fake)
        op()

        vms = self.cif.getVMs()
        expected = (
            set(vms.keys()) -
            set(_fake_vm_id(i) for i in failed_ids))
        for vm_id in expected:
            self.assertEqual(_Visitor.VMS.get(vm_id), 1)

        for vm_id in failed_ids:
            self.assertEqual(_Visitor.VMS.get(vm_id), None)

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

    def _make_fake_vms(self):
        for i in range(VM_NUM):
            vm_id = _fake_vm_id(i)
            with self.cif.vmContainerLock:
                self.cif.vmContainer[vm_id] = _FakeVM(
                    vm_id, vm_id)


@expandPermutations
class NumaInfoMonitorTests(TestCaseBase):

    def setUp(self):
        self.vm_id = _fake_vm_id(0)
        self.vm = _FakeVM(self.vm_id, self.vm_id)
        self.op = periodic.NumaInfoMonitor(self.vm)

    @permutations([
        # errcode, migrating, last_status
        [libvirt.VIR_ERR_NO_DOMAIN, True, vmstatus.UP],
        [libvirt.VIR_ERR_NO_DOMAIN, False, vmstatus.DOWN],
    ])
    def test_swallow_exceptions(self, errcode, migrating, last_status):

        def fail(*args):
            raise fake.Error(errcode)

        self.vm.updateNumaInfo = fail

        self.vm.migrating = migrating
        self.vm.lastStatus = last_status
        self.assertNotRaises(self.op)

    def test_propagate_exceptions(self):

        def fail(*args):
            raise fake.Error(libvirt.VIR_ERR_NO_DOMAIN)

        self.vm.updateNumaInfo = fail

        self.vm.migrating = False
        self.vm.lastStatus = vmstatus.UP
        self.assertRaises(libvirt.libvirtError, self.op)


def _fake_vm_id(i):
    return 'VM-%03i' % i


class _Visitor(periodic._RunnableOnVm):

    VMS = defaultdict(int)

    @property
    def required(self):
        if getattr(self._vm, 'fail_required', False):
            raise ValueError('required failed')
        return True

    @property
    def runnable(self):
        if getattr(self._vm, 'fail_runnable', False):
            raise ValueError('runnable failed')
        return True

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


class _FakeExecutor(object):

    def __init__(self, fail=False):
        self._fail = fail

    def dispatch(self, func, timeout):
        if self._fail:
            raise executor.TooManyTasks()
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

    def isMigrating(self):
        return self.migrating

    def updateNumaInfo(self):
        pass
