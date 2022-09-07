# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

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

    def test_start_with_start_time(self, fake_time):
        # We received an event.
        event_time = fake_time.time

        # The event was handled after 5 seconds...
        fake_time.time += 5
        c = time.Clock()

        # The total time includes the wait time..
        c.start("total", start_time=event_time)

        # Measure the time we waited since the event was received.
        c.start("wait", start_time=event_time)
        c.stop("wait")

        # Measure processing time.
        c.start("process")
        fake_time.time += 2
        c.stop("process")

        c.stop("total")

        assert str(c) == "<Clock(total=7.00, wait=5.00, process=2.00)>"

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
