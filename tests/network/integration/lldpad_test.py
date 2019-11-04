#
# Copyright 2017-2019 Red Hat, Inc.
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
from __future__ import division

import pytest

from vdsm.network.link.iface import iface
from vdsm.network.lldpad import lldptool

from ..nettestlib import veth_pair
from ..nettestlib import enable_lldp_on_ifaces
from .netintegtestlib import requires_systemctl


@pytest.fixture(scope='module', autouse=True)
def lldpad_service():
    if not lldptool.is_lldpad_service_running():
        pytest.skip('LLDPAD service is not running.')


class TestLldpadReportInteg(object):
    @requires_systemctl
    def test_get_lldp_tlvs(self):
        with veth_pair() as (nic1, nic2):
            iface(nic1).up()
            iface(nic2).up()
            with enable_lldp_on_ifaces((nic1, nic2), rx_only=False):
                assert lldptool.is_lldp_enabled_on_iface(nic1)
                assert lldptool.is_lldp_enabled_on_iface(nic2)
                tlvs = lldptool.get_tlvs(nic1)
                assert 3 == len(tlvs)
                expected_ttl_tlv = {
                    'type': 3,
                    'name': 'Time to Live',
                    'properties': {'time to live': '120'},
                }
                assert expected_ttl_tlv == tlvs[-1]

                tlvs = lldptool.get_tlvs(nic2)
                assert 3 == len(tlvs)
