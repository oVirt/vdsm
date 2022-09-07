# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

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
