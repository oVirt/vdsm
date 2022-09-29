# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

import pytest

from vdsm.network import errors as ne

from . import netfunctestlib as nftestlib
from .netfunctestlib import NOCHK, SetupNetworksError
from network.nettestlib import dummy_device
from network.nettestlib import Interface

NETWORK_NAME = 'test-network'
NET_1 = NETWORK_NAME + '1'
NET_2 = NETWORK_NAME + '2'
VLANID = 100
VLAN1 = VLANID + 1
VLAN2 = VLANID + 2


@pytest.fixture
def nic0():
    with dummy_device() as nic:
        yield nic


@pytest.fixture
def nic1():
    with dummy_device() as nic:
        yield nic


@pytest.fixture
def hidden_nic():
    # This nic is not visible to refresh caps
    with dummy_device(prefix='_dummy') as nic:
        yield nic


@nftestlib.parametrize_switch
class TestNetworkBasic(object):
    def test_add_net_based_on_nic(self, adapter, switch, nic0):
        NETCREATE = {NETWORK_NAME: {'nic': nic0, 'switch': switch}}
        with adapter.setupNetworks(NETCREATE, {}, NOCHK):
            adapter.assertNetwork(NETWORK_NAME, NETCREATE[NETWORK_NAME])

    def test_remove_net_based_on_nic(self, adapter, switch, nic0):
        NETCREATE = {NETWORK_NAME: {'nic': nic0, 'switch': switch}}
        NETREMOVE = {NETWORK_NAME: {'remove': True}}
        with adapter.setupNetworks(NETCREATE, {}, NOCHK):
            adapter.setupNetworks(NETREMOVE, {}, NOCHK)
            adapter.assertNoNetwork(NETWORK_NAME)

    @nftestlib.parametrize_bridged
    def test_change_vlan_tag_on_net(self, adapter, switch, bridged, nic0):
        NETCREATE = {
            NETWORK_NAME: {
                'nic': nic0,
                'switch': switch,
                'bridged': bridged,
                'vlan': VLAN1,
            }
        }
        NETEDIT = {
            NETWORK_NAME: {
                'nic': nic0,
                'switch': switch,
                'bridged': bridged,
                'vlan': VLAN2,
            }
        }
        with adapter.setupNetworks(NETCREATE, {}, NOCHK):
            adapter.setupNetworks(NETEDIT, {}, NOCHK)
            adapter.assertVlan(NETEDIT[NETWORK_NAME])

    @nftestlib.parametrize_bridged
    def test_add_net_twice(self, adapter, switch, bridged, nic0):
        NETCREATE = {
            NETWORK_NAME: {'nic': nic0, 'bridged': bridged, 'switch': switch}
        }
        with adapter.setupNetworks(NETCREATE, {}, NOCHK):
            adapter.setupNetworks(NETCREATE, {}, NOCHK)
            adapter.assertNetwork(NETWORK_NAME, NETCREATE[NETWORK_NAME])

    @nftestlib.parametrize_bridged
    def test_add_net_missing_nic_fails(self, adapter, switch, bridged):
        NETCREATE = {
            NETWORK_NAME: {
                'nic': 'missing_nic',
                'bridged': bridged,
                'switch': switch,
            }
        }
        with pytest.raises(SetupNetworksError) as cm:
            with adapter.setupNetworks(NETCREATE, {}, NOCHK):
                pass
        assert cm.value.status == ne.ERR_BAD_NIC

    def test_remove_missing_net_fails(self, adapter, switch):
        NETREMOVE = {NETWORK_NAME: {'remove': True}}
        with pytest.raises(SetupNetworksError) as cm:
            with adapter.setupNetworks(NETREMOVE, {}, NOCHK):
                pass
        assert cm.value.status == ne.ERR_BAD_BRIDGE

    def test_add_net_based_on_vlan(self, adapter, switch, nic0):
        NETCREATE = {
            NETWORK_NAME: {'nic': nic0, 'vlan': VLANID, 'switch': switch}
        }
        with adapter.setupNetworks(NETCREATE, {}, NOCHK):
            adapter.assertNetwork(NETWORK_NAME, NETCREATE[NETWORK_NAME])

    def test_remove_net_based_on_vlan(self, adapter, switch, nic0):
        NETCREATE = {
            NETWORK_NAME: {'nic': nic0, 'vlan': VLANID, 'switch': switch}
        }
        NETREMOVE = {NETWORK_NAME: {'remove': True}}
        with adapter.setupNetworks(NETCREATE, {}, NOCHK):
            adapter.setupNetworks(NETREMOVE, {}, NOCHK)
            adapter.assertNoNetwork(NETWORK_NAME)
            adapter.assertNoVlan(nic0, VLANID)

    @nftestlib.parametrize_bridged
    def test_add_net_with_multi_vlans_over_a_nic(
        self, adapter, switch, bridged, nic0
    ):
        VLAN_COUNT = 3
        netsetup = {}
        for tag in range(VLAN_COUNT):
            netname = '{}{}'.format(NETWORK_NAME, tag)
            netsetup[netname] = {
                'vlan': tag,
                'nic': nic0,
                'switch': switch,
                'bridged': bridged,
            }

        with adapter.setupNetworks(netsetup, {}, NOCHK):
            for netname, netattrs in netsetup.items():
                adapter.assertNetwork(netname, netattrs)

    def test_add_bridged_net_missing_sb_device(self, adapter, switch):
        if switch == 'ovs':
            pytest.skip('nicless bridged ovs network is currently broken.')

        NETCREATE = {NETWORK_NAME: {'bridged': True, 'switch': switch}}
        with adapter.setupNetworks(NETCREATE, {}, NOCHK):
            adapter.assertNetwork(NETWORK_NAME, NETCREATE[NETWORK_NAME])

    def test_add_bridgeless_net_missing_sb_device_fails(self, adapter, switch):
        NETCREATE = {NETWORK_NAME: {'bridged': False, 'switch': switch}}
        with pytest.raises(SetupNetworksError) as err:
            with adapter.setupNetworks(NETCREATE, {}, NOCHK):
                pass
        assert err.value.status == ne.ERR_BAD_PARAMS

    def test_add_bridged_vlaned_net_missing_sb_device_fails(
        self, adapter, switch
    ):
        NETCREATE = {
            NETWORK_NAME: {'bridged': True, 'vlan': VLANID, 'switch': switch}
        }
        with pytest.raises(SetupNetworksError) as err:
            with adapter.setupNetworks(NETCREATE, {}, NOCHK):
                pass
        assert err.value.status == ne.ERR_BAD_VLAN

    def test_add_bridgeless_vlaned_net_missing_sb_device_fails(
        self, adapter, switch
    ):
        NETCREATE = {
            NETWORK_NAME: {'bridged': False, 'vlan': VLANID, 'switch': switch}
        }
        with pytest.raises(SetupNetworksError) as err:
            with adapter.setupNetworks(NETCREATE, {}, NOCHK):
                pass
        assert err.value.status == ne.ERR_BAD_VLAN

    @nftestlib.parametrize_bridged
    def test_add_vlaned_and_non_vlaned_nets_same_nic(
        self, adapter, switch, bridged, nic0
    ):
        net_1_attrs = self._create_net_attrs(nic0, bridged, switch)
        net_2_attrs = self._create_net_attrs(nic0, bridged, switch, VLANID)

        self._assert_nets(adapter, net_1_attrs, net_2_attrs)

    @nftestlib.parametrize_bridged
    def test_add_multiple_nets_on_the_same_nic_fails(
        self, adapter, switch, bridged, nic0
    ):
        self._test_add_multiple_nets_fails(adapter, switch, bridged, nic0)

    @nftestlib.parametrize_bridged
    def test_add_identical_vlan_id_nets_same_nic_fails(
        self, adapter, switch, bridged, nic0
    ):
        self._test_add_multiple_nets_fails(
            adapter, switch, bridged, nic0, vlan_id=VLANID
        )

    @nftestlib.parametrize_bridged
    def test_add_identical_vlan_id_nets_with_two_nics(
        self, adapter, switch, bridged, nic0, nic1
    ):
        net_1_attrs = self._create_net_attrs(nic0, bridged, switch, VLANID)
        net_2_attrs = self._create_net_attrs(nic1, bridged, switch, VLANID)

        self._assert_nets(adapter, net_1_attrs, net_2_attrs)

    def test_remove_bridgeless_net_with_a_nic_used_by_a_vlan_net(
        self, adapter, switch, nic0
    ):
        netcreate = {
            NET_1: {'bridged': False, 'nic': nic0, 'switch': switch},
            NET_2: {
                'bridged': False,
                'nic': nic0,
                'vlan': VLANID,
                'switch': switch,
            },
        }

        with adapter.setupNetworks(netcreate, {}, NOCHK):
            netremove = {NET_1: {'remove': True}}
            adapter.setupNetworks(netremove, {}, NOCHK)
            adapter.assertNoNetwork(NET_1)
            adapter.assertNetwork(NET_2, netcreate[NET_2])

    def test_switch_between_bridgeless_and_bridged_vlaned_net(
        self, adapter, switch, nic0
    ):
        netattrs = {
            'nic': nic0,
            'vlan': VLANID,
            'bridged': False,
            'switch': switch,
        }
        netsetup = {NETWORK_NAME: netattrs}
        with adapter.setupNetworks(netsetup, {}, NOCHK):
            netattrs['bridged'] = True
            adapter.setupNetworks(netsetup, {}, NOCHK)
            adapter.assertNetwork(NETWORK_NAME, netattrs)
            netattrs['bridged'] = False
            adapter.setupNetworks(netsetup, {}, NOCHK)
            adapter.assertNetwork(NETWORK_NAME, netattrs)

    def test_move_vlan_from_one_iface_to_another(
        self, adapter, switch, nic0, nic1
    ):
        net_attrs = {
            'bridged': False,
            'nic': nic0,
            'vlan': VLANID,
            'switch': switch,
        }
        with adapter.setupNetworks({NET_1: net_attrs}, {}, NOCHK):
            net_attrs.update(nic=nic1)
            adapter.setupNetworks({NET_1: net_attrs}, {}, NOCHK)
            adapter.assertNetwork(NET_1, net_attrs)
            adapter.assertNoVlan(nic0, VLANID)

    def test_move_vlan_and_create_new_network_on_old_iface(
        self, adapter, switch, nic0, nic1
    ):
        initital_net_attrs = {
            'bridged': False,
            'nic': nic0,
            'vlan': VLANID,
            'switch': switch,
        }
        with adapter.setupNetworks({NET_1: initital_net_attrs}, {}, NOCHK):
            updated_net_attributes = {
                'bridged': False,
                'nic': nic1,
                'vlan': VLANID,
                'switch': switch,
            }
            with adapter.setupNetworks(
                {NET_1: updated_net_attributes, NET_2: initital_net_attrs},
                {},
                NOCHK,
            ):
                adapter.assertNetwork(NET_1, updated_net_attributes)
                adapter.assertNetwork(NET_2, initital_net_attrs)

    def _test_add_multiple_nets_fails(
        self, adapter, switch, bridged, nic, vlan_id=None
    ):
        net_1_attrs = net_2_attrs = self._create_net_attrs(
            nic, bridged, switch, vlan_id
        )
        with adapter.setupNetworks({NET_1: net_1_attrs}, {}, NOCHK):
            with pytest.raises(SetupNetworksError) as cm:
                with adapter.setupNetworks({NET_2: net_2_attrs}, {}, NOCHK):
                    pass
            assert cm.value.status == ne.ERR_BAD_PARAMS

    def _assert_nets(self, adapter, net_1_attrs, net_2_attrs):
        with adapter.setupNetworks({NET_1: net_1_attrs}, {}, NOCHK):
            with adapter.setupNetworks({NET_2: net_2_attrs}, {}, NOCHK):
                adapter.assertNetwork(NET_1, net_1_attrs)
                adapter.assertNetwork(NET_2, net_2_attrs)

    def _create_net_attrs(self, nic, bridged, switch, vlan_id=None):
        attrs = {'nic': nic, 'bridged': bridged, 'switch': switch}
        if vlan_id is not None:
            attrs['vlan'] = vlan_id

        return attrs


@pytest.mark.legacy_switch
class TestNetworkBasicLegacy(object):
    @pytest.mark.parametrize(
        "net_name", ['a' * 16, 'a b', 'a\tb', 'a.b', 'a:b']
    )
    def test_add_bridged_net_with_invalid_name_fails(
        self, adapter, net_name, nic0
    ):
        NETCREATE = {net_name: {'nic': nic0, 'switch': 'legacy'}}
        with pytest.raises(SetupNetworksError) as err:
            with adapter.setupNetworks(NETCREATE, {}, NOCHK):
                pass
        assert err.value.status == ne.ERR_BAD_BRIDGE

    @nftestlib.parametrize_bridged
    def test_replace_broken_network(self, adapter, bridged, nic0):
        NETCREATE = {
            NETWORK_NAME: {'nic': nic0, 'vlan': VLANID, 'bridged': bridged}
        }
        with adapter.setupNetworks(NETCREATE, {}, NOCHK):
            if bridged:
                dev_name = NETWORK_NAME
            else:
                dev_name = f'{nic0}.{NETCREATE[NETWORK_NAME]["vlan"]}'
            Interface.from_existing_dev_name(dev_name).remove()

            adapter.refresh_netinfo()

            adapter.assertNoNetworkExists(NETWORK_NAME)
            with adapter.setupNetworks(NETCREATE, {}, NOCHK):
                adapter.assertNetworkExists(NETWORK_NAME)

    def test_change_bridged_network_vlan_id_while_additional_port_is_attached(
        self, adapter, nic0, hidden_nic
    ):
        NETCREATE = {
            NETWORK_NAME: {
                'nic': nic0,
                'switch': 'legacy',
                'vlan': 100,
                'bridged': True,
            }
        }
        NETEDIT = {
            NETWORK_NAME: {
                'nic': nic0,
                'switch': 'legacy',
                'vlan': 110,
                'bridged': True,
            }
        }
        with adapter.setupNetworks(NETCREATE, {}, NOCHK):
            nftestlib.attach_dev_to_bridge(hidden_nic, NETWORK_NAME)
            adapter.setupNetworks(NETEDIT, {}, NOCHK)
