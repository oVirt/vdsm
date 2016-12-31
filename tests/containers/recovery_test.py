#
# Copyright 2016 Red Hat, Inc.
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

import uuid
import xml.etree.ElementTree as ET

from vdsm.virt import containers
from vdsm.virt.containers import command
from vdsm.virt.containers import xmlfile

from monkeypatch import MonkeyPatchScope

from . import conttestlib


class FakeSystemctlList(object):

    def __init__(self, vm_uuids):
        self.uuids = vm_uuids

    def __call__(self, *args, **kwargs):
        output = '\n'.join(
            'vdsm-%s.service   loaded  active  running useless' % vm_uuid
            for vm_uuid in self.uuids
        )
        return output


class FakeRepo(object):

    def __init__(self, vm_uuids):
        self.uuids = vm_uuids

    def get(self, *args, **kwargs):
        return FakeSystemctlList(self.uuids)


class RecoveryTests(conttestlib.RunnableTestCase):

    def test_recoverAllDomains(self):
        vm_uuid = str(uuid.uuid4())

        syslist = FakeSystemctlList((vm_uuid,))

        with MonkeyPatchScope([(command, 'systemctl_list', syslist)]):
            with conttestlib.tmp_run_dir():
                xf = xmlfile.XMLFile(vm_uuid)
                save_xml(xf, conttestlib.minimal_dom_xml(vm_uuid=vm_uuid))
                recovered_doms = containers.recoveryAllDomains()
                self.assertEqual(len(recovered_doms), 1)
                self.assertEqual(recovered_doms[0].UUIDString(), vm_uuid)

    def test_recoverAllDomains_with_exceptions(self):
        vm_uuids = [
            str(uuid.uuid4()),
            str(uuid.uuid4()),
            str(uuid.uuid4()),
        ]

        syslist = FakeSystemctlList([str(uuid.uuid4())] + vm_uuids[1:])

        with MonkeyPatchScope([(command, 'systemctl_list', syslist)]):
            with conttestlib.tmp_run_dir():
                for vm_uuid in vm_uuids:
                    xf = xmlfile.XMLFile(vm_uuid)
                    save_xml(xf, conttestlib.minimal_dom_xml(vm_uuid=vm_uuid))

                recovered_doms = containers.recoveryAllDomains()
                recovered_uuids = set(vm_uuids[1:])
                self.assertEqual(len(recovered_doms),
                                 len(recovered_uuids))
                for dom in recovered_doms:
                    self.assertIn(dom.UUIDString(), recovered_uuids)


def save_xml(xf, xml_str):
    root = ET.fromstring(xml_str)
    xf.save(root)
