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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#

from __future__ import print_function

import time
from contextlib import closing

import pytest

from testlib import VdsmTestCase

from vdsm.storage import asyncevent
from vdsm.storage import asyncutils


class TestLoopingCall(VdsmTestCase):

    @pytest.mark.slow
    def test_loop(self):
        with closing(asyncevent.EventLoop()) as loop:
            log = []

            def cb():
                log.append((loop.time(), lc.deadline))

            lc = asyncutils.LoopingCall(loop, cb)
            lc.start(0.1)
            loop.call_later(0.45, loop.stop)
            loop.run_forever()

        print("calls:", log, end=" ")
        self.assertEqual(len(log), 5)
        for call_time, expected_time in log:
            self.assertAlmostEqual(call_time, expected_time, delta=0.01)

    def test_stop(self):
        with closing(asyncevent.EventLoop()) as loop:
            log = []

            def cb():
                log.append((loop.time(), lc.deadline))
                lc.stop()

            lc = asyncutils.LoopingCall(loop, cb)
            lc.start(0.1)
            loop.call_later(0.2, loop.stop)
            loop.run_forever()

        print("calls:", log, end=" ")
        self.assertEqual(len(log), 1)

    def test_continue_after_errors(self):
        with closing(asyncevent.EventLoop()) as loop:
            log = []

            def cb():
                log.append((loop.time(), lc.deadline))
                raise RuntimeError("Callback failed!")

            lc = asyncutils.LoopingCall(loop, cb)
            with self.assertRaises(RuntimeError):
                lc.start(0.1)
            loop.call_later(0.15, loop.stop)
            loop.run_forever()

        print("calls:", log, end=" ")
        self.assertEqual(len(log), 2)

    def test_callback_args(self):
        with closing(asyncevent.EventLoop()) as loop:
            log = []

            def cb(*args):
                log.append(args)
                loop.stop()

            lc = asyncutils.LoopingCall(loop, cb, "a", "b")
            lc.start(0.1)
            loop.run_forever()

        print("calls:", log, end=" ")
        self.assertEqual(log, [("a", "b")])

    @pytest.mark.slow
    def test_slow_callback(self):
        with closing(asyncevent.EventLoop()) as loop:
            log = []

            def cb():
                log.append((loop.time(), lc.deadline))
                # Miss the next deadline
                time.sleep(0.1)

            lc = asyncutils.LoopingCall(loop, cb)
            lc.start(0.1)
            loop.call_later(0.45, loop.stop)
            loop.run_forever()

        # Expected calls:
        # 0.00 ok
        # 0.10 miss
        # 0.20 ok
        # 0.30 miss
        # 0.40 ok
        # 0.45 stop
        print("calls:", log, end=" ")

        self.assertEqual(len(log), 3)

    def test_stopped_stop(self):
        lc = asyncutils.LoopingCall(None, None)
        with self.assertNotRaises():
            lc.stop()

    def test_running_start(self):
        with closing(asyncevent.EventLoop()) as loop:
            lc = asyncutils.LoopingCall(loop, loop.stop)
            lc.start(0.1)
            with self.assertRaises(AssertionError):
                lc.start(0.1)
            loop.run_forever()
