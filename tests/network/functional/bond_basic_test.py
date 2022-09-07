# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

import pytest

from vdsm.network import errors as ne

from . import netfunctestlib as nftestlib
from .netfunctestlib import NOCHK, SetupNetworksError
from network.nettestlib import dummy_device
from network.nettestlib import veth_pair

BOND_NAME = 'bond1_name'
NETWORK_NAME = 'test-network'
NETWORK2_NAME = 'test-network2'


@pytest.fixture
def nic0():
    with dummy_device() as nic:
        yield nic


@pytest.fixture
def nic1():
    with dummy_device() as nic:
        yield nic


@pytest.fixture
def nic2():
    with dummy_device() as nic:
        yield nic


@pytest.fixture
def nic3():
    with dummy_device() as nic:
        yield nic


@pytest.fixture
def two_connected_pairs_of_bond_slaves():
    with veth_pair() as (n1, n2), veth_pair() as (n3, n4):
        yield [n1, n3], [n2, n4]


@nftestlib.parametrize_switch
class TestBondBasic(object):
    def test_add_bond_with_two_nics(self, adapter, switch, nic0, nic1):
        BONDCREATE = {BOND_NAME: {'nics': [nic0, nic1], 'switch': switch}}

        with adapter.setupNetworks({}, BONDCREATE, NOCHK):
            adapter.assertBond(BOND_NAME, BONDCREATE[BOND_NAME])

    def test_add_bond_with_two_nics_and_options(
        self, adapter, switch, nic0, nic1
    ):
        BONDCREATE = {
            BOND_NAME: {
                'nics': [nic0, nic1],
                'options': 'mode=3 miimon=150',
                'switch': switch,
            }
        }

        with adapter.setupNetworks({}, BONDCREATE, NOCHK):
            adapter.assertBond(BOND_NAME, BONDCREATE[BOND_NAME])

    def test_remove_bond(self, adapter, switch, nic0, nic1):
        BONDCREATE = {BOND_NAME: {'nics': [nic0, nic1], 'switch': switch}}
        BONDREMOVE = {BOND_NAME: {'remove': True}}

        with adapter.setupNetworks({}, BONDCREATE, NOCHK):
            adapter.setupNetworks({}, BONDREMOVE, NOCHK)
            adapter.assertNoBond(BOND_NAME)

    def test_remove_slaveless_bond(self, adapter, switch, nic0, nic1, nic2):
        BONDCREATE = {BOND_NAME: {'nics': [nic0], 'switch': switch}}

        with adapter.setupNetworks({}, BONDCREATE, NOCHK):
            BONDEDIT = {BOND_NAME: {'nics': [nic1, nic2], 'switch': switch}}
            adapter.setupNetworks({}, BONDEDIT, NOCHK)

        adapter.refresh_netinfo()
        adapter.assertNoBond(BOND_NAME)

    def test_change_bond_slaves(self, adapter, switch, nic0, nic1, nic2):
        BONDCREATE = {BOND_NAME: {'nics': [nic0, nic1], 'switch': switch}}
        BONDEDIT = {BOND_NAME: {'nics': [nic0, nic2], 'switch': switch}}

        with adapter.setupNetworks({}, BONDCREATE, NOCHK):
            with nftestlib.monitor_stable_link_state(BOND_NAME):
                adapter.setupNetworks({}, BONDEDIT, NOCHK)
                adapter.assertBond(BOND_NAME, BONDEDIT[BOND_NAME])

    def test_swap_slaves_between_bonds(
        self, adapter, switch, nic0, nic1, nic2, nic3
    ):
        BOND1 = BOND_NAME + '1'
        BOND2 = BOND_NAME + '2'

        BONDCREATE = {
            BOND1: {'nics': [nic0, nic1], 'switch': switch},
            BOND2: {'nics': [nic2, nic3], 'switch': switch},
        }
        BONDEDIT = {
            BOND1: {'nics': [nic0, nic2], 'switch': switch},
            BOND2: {'nics': [nic1, nic3], 'switch': switch},
        }
        with adapter.setupNetworks({}, BONDCREATE, NOCHK):
            adapter.setupNetworks({}, BONDEDIT, NOCHK)
            adapter.assertBond(BOND1, BONDEDIT[BOND1])
            adapter.assertBond(BOND2, BONDEDIT[BOND2])

    def test_resize_bond(self, adapter, switch, nic0, nic1, nic2, nic3):
        bond = {BOND_NAME: {'nics': [nic0, nic1], 'switch': switch}}
        with adapter.setupNetworks({}, bond, NOCHK):
            bond[BOND_NAME]['nics'] += [nic2, nic3]
            adapter.setupNetworks({}, bond, NOCHK)
            adapter.assertBond(BOND_NAME, bond[BOND_NAME])

            bond[BOND_NAME]['nics'].remove(nic3)
            adapter.setupNetworks({}, bond, NOCHK)
            adapter.assertBond(BOND_NAME, bond[BOND_NAME])

    def test_add_bond_with_bad_name_fails(self, adapter, switch, nic0, nic1):
        INVALID_BOND_NAMES = ('bond', 'bond bad', 'jamesbond007')
        for bond_name in INVALID_BOND_NAMES:
            BONDCREATE = {bond_name: {'nics': [nic0, nic1], 'switch': switch}}
            with pytest.raises(SetupNetworksError) as cm:
                with adapter.setupNetworks({}, BONDCREATE, NOCHK):
                    pass
            assert cm.value.status == ne.ERR_BAD_BONDING

    def test_add_bond_with_no_nics_fails(self, adapter, switch):
        BONDCREATE = {BOND_NAME: {'nics': [], 'switch': switch}}

        with pytest.raises(SetupNetworksError) as err:
            with adapter.setupNetworks({}, BONDCREATE, NOCHK):
                pass
        assert err.value.status == ne.ERR_BAD_PARAMS

    def test_add_bond_with_enforced_mac_address(
        self, adapter, switch, nic0, nic1
    ):
        HWADDRESS = 'ce:0c:46:59:c9:d1'
        BONDCREATE = {
            BOND_NAME: {
                'nics': [nic0, nic1],
                'hwaddr': HWADDRESS,
                'switch': switch,
            }
        }

        with adapter.setupNetworks({}, BONDCREATE, NOCHK):
            adapter.assertBond(BOND_NAME, BONDCREATE[BOND_NAME])

    def test_bond_slaves_order_does_not_affect_the_mac_address(
        self, adapter, switch, nic0, nic1
    ):
        bond1 = {BOND_NAME: {'nics': [nic0, nic1], 'switch': switch}}
        bond2 = {BOND_NAME: {'nics': [nic1, nic0], 'switch': switch}}

        with adapter.setupNetworks({}, bond1, NOCHK):
            bond1_hwaddr = adapter.netinfo.bondings[BOND_NAME]['hwaddr']
        with adapter.setupNetworks({}, bond2, NOCHK):
            bond2_hwaddr = adapter.netinfo.bondings[BOND_NAME]['hwaddr']

        assert bond1_hwaddr == bond2_hwaddr


@nftestlib.parametrize_switch
class TestBondOptions(object):
    def test_bond_mode_1(self, adapter, switch, nic0, nic1):
        BONDCREATE = {
            BOND_NAME: {
                'nics': [nic0, nic1],
                'options': 'mode=1 primary=' + nic0,
                'switch': switch,
            }
        }
        with adapter.setupNetworks({}, BONDCREATE, NOCHK):
            adapter.assertBond(BOND_NAME, BONDCREATE[BOND_NAME])

    def test_bond_active_slave_report(self, adapter, switch, nic0, nic1):
        BONDCREATE = {
            BOND_NAME: {
                'nics': [nic0, nic1],
                'switch': switch,
                'options': 'mode=1',
            }
        }
        BONDEDIT = {
            BOND_NAME: {
                'nics': [nic0, nic1],
                'switch': switch,
                'options': 'mode=4',
            }
        }
        with adapter.setupNetworks({}, BONDCREATE, NOCHK):
            adapter.assertBondActiveSlaveExists(BOND_NAME, [nic0, nic1])
            adapter.setupNetworks({}, BONDEDIT, NOCHK)
            adapter.assertBondNoActiveSlaveExists(BOND_NAME)

    def test_bond_mode_change(self, adapter, switch, nic0, nic1):
        BONDCREATE = {
            BOND_NAME: {
                'nics': [nic0, nic1],
                'switch': switch,
                'options': 'mode=1 miimon=150',
            }
        }
        BONDEDIT = {
            BOND_NAME: {
                'nics': [nic0, nic1],
                'switch': switch,
                'options': 'mode=3',
            }
        }
        with adapter.setupNetworks({}, BONDCREATE, NOCHK):
            adapter.setupNetworks({}, BONDEDIT, NOCHK)
            adapter.assertBond(BOND_NAME, BONDEDIT[BOND_NAME])

    def test_bond_options_with_the_mode_specified_last(
        self, adapter, switch, nic0, nic1
    ):
        BONDCREATE = {
            BOND_NAME: {
                'nics': [nic0, nic1],
                'options': 'lacp_rate=fast mode=802.3ad',
                'switch': switch,
            }
        }

        with adapter.setupNetworks({}, BONDCREATE, NOCHK):
            adapter.assertBond(BOND_NAME, BONDCREATE[BOND_NAME])

    def test_bond_arp_ip_target_change(self, adapter, switch, nic0, nic1):
        create_options = (
            'mode=1 arp_interval=1000 ' 'arp_ip_target=192.168.122.1'
        )
        BONDCREATE = {
            BOND_NAME: {
                'nics': [nic0, nic1],
                'switch': switch,
                'options': create_options,
            }
        }
        edit_options = (
            'mode=1 arp_interval=1000 ' 'arp_ip_target=10.1.3.1,10.1.2.1'
        )
        BONDEDIT = {
            BOND_NAME: {
                'nics': [nic0, nic1],
                'switch': switch,
                'options': edit_options,
            }
        }
        with adapter.setupNetworks({}, BONDCREATE, NOCHK):
            adapter.setupNetworks({}, BONDEDIT, NOCHK)
            adapter.assertBond(BOND_NAME, BONDEDIT[BOND_NAME])

    def test_bond_mode4_caps_aggregator_id(
        self, adapter, switch, two_connected_pairs_of_bond_slaves
    ):
        bond0_slaves, bond1_slaves = two_connected_pairs_of_bond_slaves
        nics = bond0_slaves + bond1_slaves
        BONDCREATE = {
            BOND_NAME
            + '0': {
                'nics': bond0_slaves,
                'options': 'mode=4 lacp_rate=1',
                'switch': switch,
            },
            BOND_NAME
            + '1': {
                'nics': bond1_slaves,
                'options': 'mode=4 lacp_rate=1',
                'switch': switch,
            },
        }
        bond0, bond1 = BONDCREATE
        with adapter.setupNetworks({}, BONDCREATE, NOCHK):
            nftestlib.wait_bonds_lp_interval()
            adapter.refresh_netinfo()

            adapter.assertLACPConfigured(BONDCREATE, nics)
            adapter.assertBondHwaddrToPartnerMac(bond0, bond1)
            adapter.assertBondHwaddrToPartnerMac(bond1, bond0)

    def test_bond_mode0_no_lacp_configuration(
        self, adapter, switch, two_connected_pairs_of_bond_slaves
    ):
        bond0_slaves, bond1_slaves = two_connected_pairs_of_bond_slaves
        nics = bond0_slaves + bond1_slaves
        BONDCREATE = {
            BOND_NAME
            + '0': {
                'nics': bond0_slaves,
                'options': 'mode=0',
                'switch': switch,
            },
            BOND_NAME
            + '1': {
                'nics': bond1_slaves,
                'options': 'mode=0',
                'switch': switch,
            },
        }
        with adapter.setupNetworks({}, BONDCREATE, NOCHK):
            nftestlib.wait_bonds_lp_interval()
            for bond_name, bond_options in BONDCREATE.items():
                adapter.assertBond(bond_name, bond_options)
            adapter.assertNoLACPConfigured(BONDCREATE, nics)
