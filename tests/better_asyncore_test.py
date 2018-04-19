#
# Copyright 2016-2017 Red Hat, Inc.
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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA
# 02110-1301  USA
#
# Refer to the README and COPYING files for full details of the license
#

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
