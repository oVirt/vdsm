#
# Copyright 2015-2016 Red Hat, Inc.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License as published
# by the Free Software Foundation; either version 2 of the License, or
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
from __future__ import absolute_import
from __future__ import division

import libvirt

from six.moves import range

from vdsm.virt.containers import connection
from vdsm.virt.containers import domain
from vdsm.virt.containers import events

from . import conttestlib


NUM = 5  # random "low" number


class ConnectionTests(conttestlib.RunnableTestCase):

    def setUp(self):
        super(ConnectionTests, self).setUp()
        conttestlib.clear_events(events.root)

    def test_without_registered(self):
        self.assertEqual(tuple(sorted(events.root.registered)),
                         tuple())

    def test_register_any(self):
        libvirt_events = (
            libvirt.VIR_DOMAIN_EVENT_ID_LIFECYCLE,
            libvirt.VIR_DOMAIN_EVENT_ID_REBOOT,
            libvirt.VIR_DOMAIN_EVENT_ID_RTC_CHANGE,
            libvirt.VIR_DOMAIN_EVENT_ID_IO_ERROR_REASON,
            libvirt.VIR_DOMAIN_EVENT_ID_GRAPHICS,
            libvirt.VIR_DOMAIN_EVENT_ID_BLOCK_JOB,
            libvirt.VIR_DOMAIN_EVENT_ID_WATCHDOG,
        )

        conn = connection.Connection()
        for ev in libvirt_events:
            conn.domainEventRegisterAny(None, ev, _handler, ev)

        self.assertEqual(tuple(sorted(libvirt_events)),
                         tuple(sorted(events.root.registered)))

    def test_register_specific_dom(self):
        evt = libvirt.VIR_DOMAIN_EVENT_ID_LIFECYCLE

        called = [False]

        def _cb(*args, **kwargs):
            called[0] = True

        conn = connection.Connection()
        dom = domain.Domain(conttestlib.minimal_dom_xml())
        conn.domainEventRegisterAny(dom, evt, _cb, None)

        # FIXME
        self.assertEqual(tuple(),
                         tuple(sorted(events.root.registered)))
        self.assertEqual((evt,),
                         tuple(sorted(dom.events.registered)))

        dom.events.fire(evt, None)
        self.assertTrue(called[0])

    def test_register_multiple_callbacks(self):
        evt = libvirt.VIR_DOMAIN_EVENT_ID_LIFECYCLE

        called = [False] * NUM

        def _cb(conn, dom, opaque):
            called[opaque] = True

        conn = connection.Connection()
        for idx in range(NUM):
            conn.domainEventRegisterAny(None, evt, _cb, None)

        self.assertFalse(all(called))
        for idx in range(NUM):
            events.fire(evt, None, idx)
        self.assertTrue(all(called))

    def test_fire_unknown_event(self):
        self.assertNotRaises(events.fire,
                             libvirt.VIR_DOMAIN_EVENT_ID_REBOOT,
                             None)

    def test_fire_unknown_event_through_dom(self):
        dom = domain.Domain(conttestlib.minimal_dom_xml())
        self.assertNotRaises(dom.events.fire,
                             libvirt.VIR_DOMAIN_EVENT_ID_REBOOT,
                             None)


def _handler(*args, **kwargs):
    pass
