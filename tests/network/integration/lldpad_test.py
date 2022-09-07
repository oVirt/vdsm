# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

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
