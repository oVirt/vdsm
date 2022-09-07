# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division
import socket
from contextlib import closing

from vdsm.common import concurrent
from yajsonrpc.betterAsyncore import AsyncoreEvent, Reactor

from testlib import VdsmTestCase as TestCaseBase


class TestEvent(TestCaseBase):

    def test_close(self):
        event = AsyncoreEvent()

        with closing(event):
            # we check that file_wrapper uses different fileno
            # than actual eventfd
            self.assertNotEqual(event.socket.fileno(), event._eventfd.fileno())

        self.assertFalse(event.closing)


class TestingImpl(object):

    def readable(self, dispatcher):
        return True

    def writable(self, dispatcher):
        return False

    def next_check_interval(self):
        return 0.1


class TestReactor(TestCaseBase):

    def test_close(self):
        reactor = Reactor()
        thread = concurrent.thread(reactor.process_requests,
                                   name='test ractor')
        thread.start()
        s1, s2 = socket.socketpair()
        with closing(s2):
            disp = reactor.create_dispatcher(s1, impl=TestingImpl())
            reactor.stop()

        thread.join(timeout=1)

        self.assertTrue(disp.closing)
        self.assertFalse(reactor._wakeupEvent.closing)
