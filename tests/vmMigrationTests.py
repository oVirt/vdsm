#
# Copyright 2014 Red Hat, Inc.
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

from itertools import tee, izip, product

from vdsm.config import config
from virt import migration

from monkeypatch import MonkeyPatchScope
from testlib import VdsmTestCase as TestCaseBase
from testlib import permutations, expandPermutations
from testlib import make_config
import vmfakelib as fake


# defaults
_DOWNTIME = config.getint('vars', 'migration_downtime')

_STEPS = config.getint('vars', 'migration_downtime_steps')

_STEPS_MIN = 2
_STEPS_HUGE = 1000

_DOWNTIME_MIN = 100
_DOWNTIME_HUGE = 10000

_PARAMS = tuple(product((_DOWNTIME_MIN, _DOWNTIME, _DOWNTIME_HUGE),
                        (_STEPS_MIN, _STEPS, _STEPS_HUGE)))


@expandPermutations
class DowntimeThreadTests(TestCaseBase):

    # No special meaning, But steps just need to be >= 2
    DOWNTIME = 1000

    @permutations([[1], [2], [10]])
    def test_update_downtime_using_n_steps(self, steps):
        downtimes = _update_downtime_repeatedly(self.DOWNTIME, steps)
        self.assertEqual(len(downtimes), steps)

    @permutations([[1], [2], [10]])
    def test_update_downtime_monotonic_increasing(self, steps):
        downtimes = _update_downtime_repeatedly(self.DOWNTIME, steps)
        self.assertTrue(sorted(downtimes), downtimes)

    @permutations([[1], [2], [10]])
    def test_update_downtime_converges(self, steps):
        downtimes = _update_downtime_repeatedly(self.DOWNTIME, steps)
        self.assertEqual(downtimes[-1], self.DOWNTIME)


@expandPermutations
class TestVmMigrationDowntimeSequence(TestCaseBase):

    @permutations(_PARAMS)
    def test_downtime_is_sequence(self, dtime, steps):
        self.assertTrue(len(self._default(dtime, steps)) >= 2)

    @permutations(_PARAMS)
    def test_downtime_increasing(self, dtime, steps):
        for a, b in pairwise(self._default(dtime, steps)):
            self.assertTrue(a <= b)

    @permutations(_PARAMS)
    def test_exponential_dowtime_never_zero(self, dtime, steps):
        for dt in self._default(dtime, steps):
            self.assertTrue(dt > 0)

    @permutations(_PARAMS)
    def test_exponential_downtime_is_lower(self, dtime, steps):
        # it's OK if exponential starts a little higher than linear...
        exp = self._default(dtime, steps)
        lin = self._linear(dtime, steps)
        self.assertAlmostEqual(exp[0], lin[0],
                               delta=self._delta(dtime, steps))

        # ...but what matters is that after that, it stays lower.
        for i, (a, b) in enumerate(zip(exp[1:], lin[1:])):
            msg = 'step=%i/%i exp=%f lin=%f' % (i+1, steps, a, b)
            self.assertTrue(a <= b, msg)

    @permutations(_PARAMS)
    def test_exponential_same_end_value(self, dtime, steps):
        exp = self._default(dtime, steps)
        lin = self._linear(dtime, steps)
        self.assertAlmostEqual(exp[-1], lin[-1],
                               delta=self._delta(dtime, steps))

    @permutations(_PARAMS)
    def test_end_value_is_maximum(self, dtime, steps):
        exp = self._default(dtime, steps)
        self.assertAlmostEqual(exp[-1], dtime,
                               delta=self._delta(dtime, steps))

    # helpers

    def _delta(self, downtime, steps):
        """
        for near-equality checks. One tenth of one step to be sure.
        However, downtime is in milliseconds, so it is fair to
        have a lower bound here.
        """
        return max(1, (downtime / steps) / 10.)

    def _default(self, downtime, steps):
        """provides the default downtime sequence"""
        return list(migration.exponential_downtime(downtime, steps))

    def _linear(self, downtime, steps):
        return list(_linear_downtime(downtime, steps))


class MigrationParamsTests(TestCaseBase):

    def setUp(self):
        # random values, no real meaning
        self.params = {
            'foo': 'bar',
            'answer': 42,
            'hyperv': ['qemu', 'kvm'],
        }

    def test_params_stored(self):
        with fake.VM() as testvm:
            with testvm.migration_parameters(self.params):
                self.assertEquals(testvm.conf['_migrationParams'],
                                  self.params)

    def test_params_removed(self):
        with fake.VM() as testvm:
            with testvm.migration_parameters(self.params):
                pass

            self.assertNotIn('_migrationParams', testvm.conf)


# stolen^Wborrowed from itertools recipes
def pairwise(iterable):
    "s -> (s0,s1), (s1,s2), (s2, s3), ..."
    a, b = tee(iterable)
    next(b, None)
    return izip(a, b)


def _linear_downtime(downtime, steps):
    "this is the old formula as reference"
    for i in range(steps):
        # however, it makes no sense to have less than 1 ms
        # we want to avoid anyway downtime = 0
        yield max(1, downtime * (i + 1) / steps)


def _update_downtime_repeatedly(downtime, steps):
        dom = fake.Domain()

        with fake.VM({'memSize': 1024}) as testvm:
            testvm._dom = dom

            cfg = make_config([('vars', 'migration_downtime_delay', '0')])
            with MonkeyPatchScope([(migration, 'config', cfg)]):
                dt = migration.DowntimeThread(testvm, downtime, steps)
                dt.start()
                dt.join()

                return dom.getDowntimes()
