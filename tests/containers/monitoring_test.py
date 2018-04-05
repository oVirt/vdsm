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

from vdsm.virt.containers import connection
from vdsm.virt.containers import monitoring

from . import conttestlib


class MonitoringTests(conttestlib.RunnableTestCase):

    def test_domain_disappeared(self):
        evt = libvirt.VIR_DOMAIN_EVENT_ID_LIFECYCLE

        delivered = []

        def _cb(*args, **kwargs):
            delivered.append(args)

        conn = connection.Connection()
        with conttestlib.tmp_run_dir():
            dom = conn.defineXML(conttestlib.minimal_dom_xml(), 0)
            conn.domainEventRegisterAny(dom, evt, _cb, None)
            monitoring.watchdog(lambda: [])

        self.assertEqual(delivered, [(
            conn,
            dom,
            libvirt.VIR_DOMAIN_EVENT_STOPPED,
            libvirt.VIR_DOMAIN_EVENT_STOPPED_SHUTDOWN,
        )])

    def test_domain_all_present(self):
        evt = libvirt.VIR_DOMAIN_EVENT_ID_LIFECYCLE

        delivered = []

        def _cb(*args, **kwargs):
            delivered.append(args)

        conn = connection.Connection()
        with conttestlib.tmp_run_dir():
            dom = conn.defineXML(conttestlib.minimal_dom_xml(), 0)
            conn.domainEventRegisterAny(dom, evt, _cb, None)

            def _fake_get_all():
                return [dom.runtimeUUIDString()]

            monitoring.watchdog(_fake_get_all)

        self.assertEqual(delivered, [])


def _handler(*args, **kwargs):
    pass
