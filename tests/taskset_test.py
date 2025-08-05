# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

import multiprocessing
import os
import tempfile

import pytest

from vdsm import taskset
from vdsm.common import cmdutils

from monkeypatch import MonkeyPatchScope
from testlib import VdsmTestCase
from testlib import permutations, expandPermutations
import testlib


_CPU_COMBINATIONS = (
    [frozenset((0,))],
    [frozenset((0, 3,))],
    [frozenset((1, 2,))],
)


@expandPermutations
class AffinityTests(VdsmTestCase):

    def setUp(self):
        self.running = multiprocessing.Event()
        self.stop = multiprocessing.Event()
        self.proc = None

    def tearDown(self):
        self.stop.set()
        if self.proc is not None:
            self.proc.terminate()
            self.proc.join()

    def test_get(self):

        self.proc = multiprocessing.Process(target=self._run_child)
        self.proc.start()
        if not self.running.wait(0.5):
            raise RuntimeError("helper child process not running!")

        self.assertEqual(taskset.get(self.proc.pid),
                         taskset.get(os.getpid()))

    @permutations(_CPU_COMBINATIONS)
    def test_set_from_parent(self, cpu_set):

        validate_running_with_enough_cpus(cpu_set)

        self.proc = multiprocessing.Process(target=self._run_child)
        self.proc.start()
        if not self.running.wait(0.5):
            raise RuntimeError("helper child process not running!")

        taskset.set(self.proc.pid, cpu_set)
        self.assertEqual(taskset.get(self.proc.pid), cpu_set)

    @permutations(_CPU_COMBINATIONS)
    def test_set_from_child(self, cpu_set):

        validate_running_with_enough_cpus(cpu_set)

        self.proc = multiprocessing.Process(target=self._run_child,
                                            args=(cpu_set,))
        self.proc.start()
        if not self.running.wait(0.5):
            raise RuntimeError("helper child process not running!")

        self.assertEqual(taskset.get(self.proc.pid), cpu_set)

    def test_get_raises_on_failure(self):
        # here we just need to feed taskset with any bad input.
        self.assertRaises(cmdutils.Error, taskset.get, '')

    def test_set_raises_on_failure(self):
        # here we just need to feed taskset with any bad input.
        self.assertRaises(cmdutils.Error, taskset.set, '', 'x')

    def _run_child(self, cpu_set=None):
        if cpu_set:
            taskset.set(os.getpid(), cpu_set)
        self.running.set()
        self.stop.wait()


@expandPermutations
class OnlineCpusFunctionsTests(VdsmTestCase):

    @permutations([
        # raw_value, cpu_set
        [b'0', set((0,))],
        [b'0,1,2,3', set(range(4))],
        [b'0-3', set(range(4))],
        [b'0-1,3', set((0, 1, 3))],
        [b'0-2,5-7', set((0, 1, 2, 5, 6, 7))],
        # as seen on ppc64 20151130
        [b'8,16,24,32,40,48,56,64,72,80,88,96,104,112,120,128,136,144,152',
         set((8, 16, 24, 32, 40, 48, 56, 64, 72, 80,
              88, 96, 104, 112, 120, 128, 136, 144, 152))],
    ])
    def test_online_cpus(self, raw_value, cpu_set):

        with tempfile.NamedTemporaryFile() as f:
            f.write(raw_value + b'\n')
            f.flush()
            with MonkeyPatchScope([(taskset, "_SYS_ONLINE_CPUS", f.name)]):
                self.assertEqual(taskset.online_cpus(), cpu_set)

    @permutations([
        # cpu_set, expected
        [frozenset((0,)), 0],
        [frozenset((1,)), 1],
        [frozenset(range(4)), 1],
        [frozenset(range(1, 4)), 2],
        [frozenset(range(3, 9)), 4],
    ])
    def test_pick_cpu(self, cpu_set, expected):
        self.assertEqual(taskset.pick_cpu(cpu_set), expected)


# TODO: find a clean way to make this a decorator
def validate_running_with_enough_cpus(cpu_set):
    max_available_cpu = sorted(testlib.online_cpus())[-1]
    max_required_cpu = sorted(cpu_set)[-1]

    if max_available_cpu < max_required_cpu:
        pytest.skip(
            "This test requires at least %i available CPUs"
            " (running with %i)" % (max_required_cpu, max_available_cpu))
