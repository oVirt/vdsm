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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#

import multiprocessing
import os

from nose.plugins.skip import SkipTest

from vdsm import cmdutils
from vdsm import taskset

from testlib import online_cpus
from testlib import VdsmTestCase
from testlib import permutations, expandPermutations


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


# TODO: find a clean way to make this a decorator
def validate_running_with_enough_cpus(cpu_set):
    max_available_cpu = sorted(online_cpus())[-1]
    max_required_cpu = sorted(cpu_set)[-1]

    if max_available_cpu < max_required_cpu:
        raise SkipTest(
            "This test requires at least %i available CPUs"
            " (running with %i)" % (max_required_cpu, max_available_cpu))
