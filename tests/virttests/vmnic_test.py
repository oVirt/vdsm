#
# Copyright 2017 Red Hat, Inc.
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

from __future__ import absolute_import

import logging

from vdsm.virt.vmdevices import network
from vdsm.virt import vmdevices
from vdsm.virt import vmxml

from testlib import permutations, expandPermutations
from testlib import XMLTestCase
from monkeypatch import MonkeyPatchScope


@expandPermutations
class TestNicDevice(XMLTestCase):

    def setUp(self):
        self.log = logging.getLogger('test.virt.nic')

    @permutations([
        # base_spec_params:
        [{}],
        [{'inbound': {'average': 512}, 'outbound': {'average': 512}}],
    ])
    def test_update_bandwidth_xml(self, base_spec_params):
        specParams = {
            'inbound': {
                'average': 1000,
                'peak': 5000,
                'floor': 200,
                'burst': 1024,
            },
            'outbound': {
                'average': 128,
                'peak': 256,
                'burst': 256,
            },
        }
        conf = {
            'device': 'network',
            'macAddr': 'fake',
            'network': 'default',
            'specParams': base_spec_params,
        }
        XML = u"""
        <interface type='network'>
          <mac address="fake" />
          <source bridge='default'/>
          <bandwidth>
            <inbound average='1000' peak='5000' floor='200' burst='1024'/>
            <outbound average='128' peak='256' burst='256'/>
          </bandwidth>
        </interface>
        """
        with MonkeyPatchScope([(network, 'supervdsm', FakeSuperVdsm())]):
            dev = vmdevices.network.Interface({}, self.log, **conf)
            vnic_xml = dev.getXML()
            vmdevices.network.update_bandwidth_xml(dev, vnic_xml, specParams)
            self.assertXMLEqual(vmxml.format_xml(vnic_xml), XML)


class FakeSuperVdsm(object):

    def getProxy(self):
        return self

    def ovs_bridge(self, network):
        return False
