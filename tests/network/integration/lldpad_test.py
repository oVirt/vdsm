#
# Copyright 2017-2020 Red Hat, Inc.
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

import pytest

from network.nettestlib import veth_pair
from network.nettestlib import enable_lldp_on_ifaces

from vdsm.network.link.iface import iface
from vdsm.network.lldpad import lldptool

from .netintegtestlib import requires_systemctl


@pytest.fixture(scope='module', autouse=True)
def lldpad_service():
    requires_systemctl()
    if not lldptool.is_lldpad_service_running():
        pytest.skip('LLDPAD service is not running.')


@pytest.fixture
def veth_nics():
    with veth_pair() as nics:
        for nic in nics:
            iface(nic).up()
        yield nics


@pytest.fixture
def lldp_nics(veth_nics):
    with enable_lldp_on_ifaces(veth_nics, rx_only=False):
        yield veth_nics


class TestLldpadReportInteg(object):
    def test_get_lldp_tlvs(self, lldp_nics):
        assert lldptool.is_lldp_enabled_on_iface(lldp_nics[0])
        assert lldptool.is_lldp_enabled_on_iface(lldp_nics[1])
        tlvs = lldptool.get_tlvs(lldp_nics[0])
        assert 3 == len(tlvs)
        expected_ttl_tlv = {
            'type': 3,
            'name': 'Time to Live',
            'properties': {'time to live': '120'},
        }
        assert expected_ttl_tlv == tlvs[-1]

        tlvs = lldptool.get_tlvs(lldp_nics[1])
        assert 3 == len(tlvs)
