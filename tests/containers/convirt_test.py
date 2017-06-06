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

from contextlib import contextmanager
import errno
import os.path

import libvirt


from vdsm.virt import containers
from vdsm.virt.containers import connection
from vdsm.virt.containers import xmlfile
from monkeypatch import MonkeyPatchScope

from . import conttestlib


@contextmanager
def tmp_state_dir():
    with conttestlib.named_temp_dir() as tmp_dir:
        with MonkeyPatchScope([
            (xmlfile, 'STATE_DIR', os.path.join(tmp_dir, 'containers')),
        ]):
            yield tmp_dir


class APITests(conttestlib.RunnableTestCase):

    # TODO: add test with found (monkeypatched)
    def test_monitor_domains_missing(self):
        evt = libvirt.VIR_DOMAIN_EVENT_ID_LIFECYCLE

        delivered = []

        def _cb(*args, **kwargs):
            delivered.append(args)

        conn = connection.Connection()
        with conttestlib.tmp_run_dir():
            dom = conn.createXML(conttestlib.minimal_dom_xml(), 0)
            conn.domainEventRegisterAny(dom, evt, _cb, None)
            containers.monitorAllDomains()

        expected = [(
            conn,
            dom,
            libvirt.VIR_DOMAIN_EVENT_STOPPED,
            libvirt.VIR_DOMAIN_EVENT_STOPPED_SHUTDOWN
        ), ]

        self.assertEqual(delivered, expected)

    def test_prepare_succeed(self):
        with tmp_state_dir():
            self.assertNotRaises(containers.prepare)

    def test_prepare_twice(self):
        with tmp_state_dir():
            containers.prepare()
            self.assertNotRaises(containers.prepare)

    def test_prepare_fails(self):
        def _makedirs(*args):
            ex = OSError()
            ex.errno = errno.EPERM
            raise ex

        with tmp_state_dir():
            containers.prepare()
            with MonkeyPatchScope([(os, 'makedirs', _makedirs)]):
                self.assertRaises(OSError,
                                  containers.prepare)


def _handler(*args, **kwargs):
    pass
