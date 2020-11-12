#
# Copyright 2018-2020 Red Hat, Inc.
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

from vdsm.virt import events

from testlib import VdsmTestCase as TestCaseBase


class TestEventName(TestCaseBase):

    def test_known(self):
        for event_id in events.LIBVIRT_EVENTS:
            assert events.event_name(event_id)

    def test_unknown(self):
        UNKNOWN_FAKE_EVENT_ID = 424242
        # given unknown events, it must still return a meaningful string)
        assert UNKNOWN_FAKE_EVENT_ID not in events.LIBVIRT_EVENTS
        assert events.event_name(UNKNOWN_FAKE_EVENT_ID)
