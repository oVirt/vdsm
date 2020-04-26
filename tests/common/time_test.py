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

import pytest
from vdsm.common import time


class FakeTime(object):

    def __init__(self, value=0):
        self.time = value

    def __call__(self):
        return self.time


@pytest.fixture
def fake_time(monkeypatch):
    fake_time = FakeTime()
    monkeypatch.setattr(time, "monotonic_time", fake_time)
    return fake_time


class TestClock:

    def test_no_timers(self):
        c = time.Clock()
        assert str(c) == "<Clock()>"

    # Ccorrect usage

    def test_start_and_stop(self, fake_time):
        c = time.Clock()
        c.start("total")
        c.start("step1")
        fake_time.time += 3
        c.stop("step1")
        c.start("step2")
        fake_time.time += 4
        c.stop("step2")
        c.stop("total")
        assert str(c) == "<Clock(total=7.00, step1=3.00, step2=4.00)>"

    def test_running(self, fake_time):
        c = time.Clock()
        c.start("foo")
        fake_time.time += 3
        c.start("bar")
        fake_time.time += 4
        c.stop("foo")
        assert str(c) == "<Clock(foo=7.00, bar=4.00*)>"

    def test_run(self, fake_time):
        c = time.Clock()
        with c.run("foo"):
            fake_time.time += 3
        assert str(c) == "<Clock(foo=3.00)>"

    def test_run_nested(self, fake_time):
        c = time.Clock()
        with c.run("outer"):
            fake_time.time += 3
            with c.run("inner"):
                fake_time.time += 4
        assert str(c) == "<Clock(outer=7.00, inner=4.00)>"

    # Inccorrect usage

    def test_start_started_clock(self):
        c = time.Clock()
        c.start("started")
        with pytest.raises(RuntimeError):
            c.start("started")

    def test_stop_stooped_clock(self):
        c = time.Clock()
        c.start("stopped")
        c.stop("stopped")
        with pytest.raises(RuntimeError):
            c.stop("stopped")

    def test_stop_missing_clock(self):
        c = time.Clock()
        with pytest.raises(RuntimeError):
            c.stop("foo")

    def test_run_started(self):
        c = time.Clock()
        c.start("started")
        with pytest.raises(RuntimeError):
            with c.run("started"):
                pass

    def test_run_stopped(self):
        c = time.Clock()
        with c.run("stopped"):
            pass
        with pytest.raises(RuntimeError):
            with c.run("stopped"):
                pass
