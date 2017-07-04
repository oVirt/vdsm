#
# Copyright 2017 Red Hat, Inc.
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

from vdsm.common import time
from testlib import VdsmTestCase
from monkeypatch import MonkeyPatch


class FakeTime(object):

    def __init__(self, value=0):
        self.time = value

    def __call__(self):
        return self.time


class TestClock(VdsmTestCase):

    def test_no_timers(self):
        c = time.Clock()
        self.assertEqual(str(c), "<Clock()>")

    # Ccorrect usage

    @MonkeyPatch(time, "monotonic_time", FakeTime())
    def test_start_and_stop(self):
        c = time.Clock()
        c.start("total")
        c.start("step1")
        time.monotonic_time.time += 3
        c.stop("step1")
        c.start("step2")
        time.monotonic_time.time += 4
        c.stop("step2")
        c.stop("total")
        self.assertEqual(str(c), "<Clock(total=7.00, step1=3.00, step2=4.00)>")

    @MonkeyPatch(time, "monotonic_time", FakeTime())
    def test_running(self):
        c = time.Clock()
        c.start("foo")
        time.monotonic_time.time += 3
        c.start("bar")
        time.monotonic_time.time += 4
        c.stop("foo")
        self.assertEqual(str(c), "<Clock(foo=7.00, bar=4.00*)>")

    @MonkeyPatch(time, "monotonic_time", FakeTime())
    def test_run(self):
        c = time.Clock()
        with c.run("foo"):
            time.monotonic_time.time += 3
        self.assertEqual(str(c), "<Clock(foo=3.00)>")

    @MonkeyPatch(time, "monotonic_time", FakeTime())
    def test_run_nested(self):
        c = time.Clock()
        with c.run("outer"):
            time.monotonic_time.time += 3
            with c.run("inner"):
                time.monotonic_time.time += 4
        self.assertEqual(str(c), "<Clock(outer=7.00, inner=4.00)>")

    # Inccorrect usage

    def test_start_started_clock(self):
        c = time.Clock()
        c.start("started")
        with self.assertRaises(RuntimeError):
            c.start("started")

    def test_stop_stooped_clock(self):
        c = time.Clock()
        c.start("stopped")
        c.stop("stopped")
        with self.assertRaises(RuntimeError):
            c.stop("stopped")

    def test_stop_missing_clock(self):
        c = time.Clock()
        self.assertRaises(RuntimeError, c.stop, "foo")

    def test_run_started(self):
        c = time.Clock()
        c.start("started")
        with self.assertRaises(RuntimeError):
            with c.run("started"):
                pass

    def test_run_stopped(self):
        c = time.Clock()
        with c.run("stopped"):
            pass
        with self.assertRaises(RuntimeError):
            with c.run("stopped"):
                pass
