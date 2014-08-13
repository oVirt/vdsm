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

from virt import vm
from virt import vmxml
import caps

from testlib import VdsmTestCase as TestCaseBase
from testlib import permutations, expandPermutations

from vmTests import FakeDomain

from vmTestsData import CONF_TO_DOMXML_X86_64
from vmTestsData import CONF_TO_DOMXML_PPC64
from vmTestsData import CONF_TO_DOMXML_NO_VDSM


@expandPermutations
class TestVmXmlFunctions(TestCaseBase):

    @permutations([[caps.Architecture.X86_64], [caps.Architecture.PPC64]])
    def test_all_channels_vdsm_domain(self, arch):
        for _, dom_xml in self._build_domain_xml(arch):
            channels = list(vmxml.all_channels(dom_xml))
            self.assertTrue(len(channels) >= len(vm._AGENT_CHANNEL_DEVICES))
            for name, path in channels:
                self.assertIn(name, vm._AGENT_CHANNEL_DEVICES)

    def test_all_channels_extra_domain(self):
        for conf, raw_xml in CONF_TO_DOMXML_NO_VDSM:
            dom_xml = raw_xml % conf
            self.assertNotEquals(sorted(vmxml.all_channels(dom_xml)),
                                 sorted(vm._AGENT_CHANNEL_DEVICES))

    _CONFS = {
        caps.Architecture.X86_64: CONF_TO_DOMXML_X86_64,
        caps.Architecture.PPC64: CONF_TO_DOMXML_PPC64,
        'novdsm': CONF_TO_DOMXML_NO_VDSM}

    def _build_domain_xml(self, arch):
        for conf, rawXml in self._CONFS[arch]:
            domXml = rawXml % conf
            yield FakeDomain(domXml, vmId=conf['vmId']), domXml
