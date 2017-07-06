#
# Copyright 2014 Red Hat, Inc.
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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#

from contextlib import contextmanager
import random

from vdsm.network import ipwrapper
from vdsm.virt import sampling

from testValidation import ValidateRunningAsRoot
from testlib import VdsmTestCase as TestCaseBase
from monkeypatch import MonkeyPatchScope
from network.nettestlib import dummy_device


class InterfaceSampleTests(TestCaseBase):
    def setUp(self):
        self.NEW_VLAN = 'vlan_%s' % (random.randint(0, 1000))

    @ValidateRunningAsRoot
    def testHostSampleReportsNewInterface(self):
        interfaces_before = set(
            sampling._get_interfaces_and_samples().iterkeys())

        with dummy_device() as dummy_name:
            interfaces_after = set(
                sampling._get_interfaces_and_samples().iterkeys())
            interfaces_diff = interfaces_after - interfaces_before
            self.assertEqual(interfaces_diff, {dummy_name})

    @ValidateRunningAsRoot
    def testHostSampleHandlesDisappearingVlanInterfaces(self):
        original_getLinks = ipwrapper.getLinks

        def faultyGetLinks():
            all_links = list(original_getLinks())
            ipwrapper.linkDel(self.NEW_VLAN)
            return iter(all_links)

        with MonkeyPatchScope([(ipwrapper, 'getLinks', faultyGetLinks)]):
            with dummy_device() as dummy_name, vlan(
                    self.NEW_VLAN, dummy_name, 999):
                interfaces_and_samples = sampling._get_interfaces_and_samples()
                self.assertNotIn(self.NEW_VLAN, interfaces_and_samples)


@contextmanager
def vlan(name, link, vlan_id):
    ipwrapper.linkAdd(name, 'vlan', link=link, args=['id', str(vlan_id)])
    try:
        yield
    finally:
        try:
            ipwrapper.linkDel(name)
        except ipwrapper.IPRoute2Error:
            # faultyGetLinks is expected to have already removed the vlan
            # device.
            pass
