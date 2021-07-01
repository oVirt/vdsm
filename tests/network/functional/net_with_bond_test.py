# Copyright 2016-2021 Red Hat, Inc.
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

from vdsm.network import errors as ne

from . import netfunctestlib as nftestlib
from .netfunctestlib import SetupNetworksError, NOCHK
from network.nettestlib import Bond
from network.nettestlib import bond_device
from network.nettestlib import dummy_device
from network.nettestlib import Interface
from network.nettestlib import IpFamily
from network.nettestlib import running_on_ovirt_ci
from network.nettestlib import vlan_device


HWADDRESS = 'ce:0c:46:59:c9:d1'
NETWORK1_NAME = 'test-network1'
NETWORK2_NAME = 'test-network2'
BOND_NAME = 'bond1'
VLAN1 = 10
VLAN2 = 20


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
def bond0_with_slaves(nic0, nic1):
    slaves = [nic0, nic1]
    with bond_device(slaves) as bond:
        yield bond, slaves


# Special case when we don't want to call delete on bond, is bond that
# was taken over by vdsm. This bond needs to be deleted through setup networks.
def bond_without_remove(slaves):
    bond = Bond(max_length=11)
    bond.create()
    for dev in slaves:
        bond.add_slave(dev)
    return bond.dev_name


@nftestlib.parametrize_switch
class TestNetworkWithBond(object):
    def test_add_the_same_nic_to_net_and_bond_in_one_step(
        self, adapter, switch, nic0
    ):
        NETCREATE = {NETWORK1_NAME: {'nic': nic0, 'switch': switch}}
        BONDCREATE = {BOND_NAME: {'nics': [nic0], 'switch': switch}}

        with pytest.raises(SetupNetworksError) as e:
            adapter.setupNetworks(NETCREATE, BONDCREATE, NOCHK)
        assert e.value.status == ne.ERR_USED_NIC

    def test_add_bond_with_nic_that_is_already_used_by_network(
        self, adapter, switch, nic0
    ):
        NETCREATE = {NETWORK1_NAME: {'nic': nic0, 'switch': switch}}
        BONDCREATE = {BOND_NAME: {'nics': [nic0], 'switch': switch}}

        with adapter.setupNetworks(NETCREATE, {}, NOCHK):
            with pytest.raises(SetupNetworksError) as e:
                adapter.setupNetworks({}, BONDCREATE, NOCHK)
            assert e.value.status == ne.ERR_USED_NIC

    def test_add_network_with_nic_that_is_already_used_by_bond(
        self, adapter, switch, nic0
    ):
        NETCREATE = {NETWORK1_NAME: {'nic': nic0, 'switch': switch}}
        BONDCREATE = {BOND_NAME: {'nics': [nic0], 'switch': switch}}

        with adapter.setupNetworks({}, BONDCREATE, NOCHK):
            with pytest.raises(SetupNetworksError) as e:
                adapter.setupNetworks(NETCREATE, {}, NOCHK)
            assert e.value.status == ne.ERR_USED_NIC

    def test_remove_bridged_net_and_keep_bond(
        self, adapter, switch, nic0, nic1
    ):
        NETCREATE = {NETWORK1_NAME: {'bonding': BOND_NAME, 'switch': switch}}
        BONDCREATE = {BOND_NAME: {'nics': [nic0, nic1], 'switch': switch}}

        with adapter.setupNetworks(NETCREATE, BONDCREATE, NOCHK):
            NETCREATE[NETWORK1_NAME] = {'remove': True}
            adapter.setupNetworks(NETCREATE, {}, NOCHK)

            adapter.assertNoNetwork(NETWORK1_NAME)

    @nftestlib.parametrize_bridged
    def test_given_bonded_net_transfer_one_slave_to_new_net(
        self, adapter, switch, bridged, nic0, nic1, nic2
    ):
        NETBASE = {
            NETWORK1_NAME: {
                'bonding': BOND_NAME,
                'bridged': bridged,
                'switch': switch,
            }
        }
        BONDBASE = {BOND_NAME: {'nics': [nic0, nic1, nic2], 'switch': switch}}

        with adapter.setupNetworks(NETBASE, BONDBASE, NOCHK):
            NETNEW = {
                NETWORK2_NAME: {
                    'nic': nic2,
                    'bridged': bridged,
                    'switch': switch,
                }
            }
            BONDEDIT = {BOND_NAME: {'nics': [nic0, nic1], 'switch': switch}}
            with nftestlib.monitor_stable_link_state(BOND_NAME):
                adapter.setupNetworks({}, BONDEDIT, NOCHK)
            with adapter.setupNetworks(NETNEW, {}, NOCHK):
                adapter.assertNetwork(NETWORK1_NAME, NETBASE[NETWORK1_NAME])
                adapter.assertNetwork(NETWORK2_NAME, NETNEW[NETWORK2_NAME])
                adapter.assertBond(BOND_NAME, BONDEDIT[BOND_NAME])

    @nftestlib.parametrize_bridged
    def test_given_bonded_net_replace_bond_with_a_slave(
        self, adapter, switch, bridged, nic0, nic1
    ):
        NETBASE = {
            NETWORK1_NAME: {
                'bonding': BOND_NAME,
                'bridged': bridged,
                'switch': switch,
            }
        }
        BONDBASE = {BOND_NAME: {'nics': [nic0, nic1], 'switch': switch}}

        with adapter.setupNetworks(NETBASE, BONDBASE, NOCHK):
            NETBASE[NETWORK1_NAME] = {
                'nic': nic0,
                'bridged': bridged,
                'switch': switch,
            }
            BONDBASE[BOND_NAME] = {'remove': True}
            adapter.setupNetworks(NETBASE, BONDBASE, NOCHK)

            adapter.assertNetwork(NETWORK1_NAME, NETBASE[NETWORK1_NAME])
            adapter.assertNoBond(BOND_NAME)

    def test_add_net_with_invalid_bond_name_fails(self, adapter, switch):
        INVALID_BOND_NAMES = ('bond', 'bond bad', 'jamesbond007')

        for bond_name in INVALID_BOND_NAMES:
            NETCREATE = {
                NETWORK1_NAME: {'bonding': bond_name, 'switch': switch}
            }
            with pytest.raises(SetupNetworksError) as cm:
                with adapter.setupNetworks(NETCREATE, {}, NOCHK):
                    pass
            assert cm.value.status == ne.ERR_BAD_BONDING

    @nftestlib.parametrize_bridged
    def test_add_net_with_multi_vlans_over_a_bond(
        self, adapter, switch, bridged, nic0, nic1
    ):
        netsetup = {}
        VLAN_COUNT = 3
        for tag in range(VLAN_COUNT):
            net_name = '{}{}'.format(NETWORK1_NAME, tag)
            netsetup[net_name] = {
                'vlan': tag,
                'bonding': BOND_NAME,
                'bridged': bridged,
                'switch': switch,
            }
        BONDCREATE = {BOND_NAME: {'nics': [nic0, nic1], 'switch': switch}}

        with adapter.setupNetworks(netsetup, BONDCREATE, NOCHK):
            for netname, netattrs in netsetup.items():
                adapter.assertNetwork(netname, netattrs)

    @nftestlib.parametrize_bridged
    def test_remove_bond_under_network(self, adapter, switch, bridged, nic0):
        NETCREATE = {
            NETWORK1_NAME: {
                'bonding': BOND_NAME,
                'bridged': bridged,
                'switch': switch,
            }
        }
        BONDCREATE = {BOND_NAME: {'nics': [nic0], 'switch': switch}}
        with adapter.setupNetworks(NETCREATE, BONDCREATE, NOCHK):

            BONDEDIT = {BOND_NAME: {'remove': True}}
            with pytest.raises(SetupNetworksError) as err:
                adapter.setupNetworks({}, BONDEDIT, NOCHK)
            assert err.value.status == ne.ERR_USED_BOND

            adapter.assertNetwork(NETWORK1_NAME, NETCREATE[NETWORK1_NAME])
            adapter.assertBond(BOND_NAME, BONDCREATE[BOND_NAME])

    def test_remove_bonded_network_while_a_slave_is_missing(
        self, adapter, switch, nic0
    ):
        NETCREATE = {
            NETWORK1_NAME: {
                'bonding': BOND_NAME,
                'bridged': False,
                'switch': switch,
            }
        }
        BONDCREATE = {BOND_NAME: {'nics': [nic0], 'switch': switch}}

        with adapter.setupNetworks(NETCREATE, BONDCREATE, NOCHK):
            with dummy_device() as nic1:
                BONDEDIT = {BOND_NAME: {'nics': [nic1], 'switch': switch}}
                adapter.setupNetworks({}, BONDEDIT, NOCHK)

            adapter.setupNetworks(
                {NETWORK1_NAME: {'remove': True}},
                {BOND_NAME: {'remove': True}},
                NOCHK,
            )

            adapter.assertNoNetwork(NETWORK1_NAME)
            adapter.assertNoBond(BOND_NAME)

    @nftestlib.parametrize_bridged
    @pytest.mark.parametrize('vlan', [False, True], ids=['non-vlan', 'vlan'])
    def test_replace_network_nic_with_bond_that_includes_the_nic(
        self, adapter, switch, bridged, vlan, nic0, nic1
    ):
        net_attrs = {'bridged': bridged, 'switch': switch, 'nic': nic1}
        bond_attrs = {'nics': [nic0, nic1], 'switch': switch}
        if vlan:
            net_attrs['vlan'] = VLAN1

        with adapter.setupNetworks({NETWORK1_NAME: net_attrs}, {}, NOCHK):
            net_attrs.pop('nic')
            net_attrs['bonding'] = BOND_NAME
            with adapter.setupNetworks(
                {NETWORK1_NAME: net_attrs}, {BOND_NAME: bond_attrs}, NOCHK
            ):
                adapter.assertNetwork(NETWORK1_NAME, net_attrs)
                adapter.assertBond(BOND_NAME, bond_attrs)


@nftestlib.parametrize_switch
class TestReuseBond(object):
    def test_detach_used_bond_from_bridge(self, adapter, switch, nic0):
        NETCREATE = {
            NETWORK1_NAME: {'bonding': BOND_NAME, 'switch': switch},
            NETWORK2_NAME: {
                'bonding': BOND_NAME,
                'vlan': VLAN2,
                'switch': switch,
            },
        }
        BONDCREATE = {BOND_NAME: {'nics': [nic0], 'switch': switch}}

        with adapter.setupNetworks(NETCREATE, BONDCREATE, NOCHK):
            NETEDIT = {
                NETWORK1_NAME: {
                    'bonding': BOND_NAME,
                    'vlan': VLAN1,
                    'switch': switch,
                }
            }
            adapter.setupNetworks(NETEDIT, {}, NOCHK)
            adapter.assertBond(BOND_NAME, BONDCREATE[BOND_NAME])

    @nftestlib.parametrize_bridged
    def test_add_vlaned_network_on_existing_bond(
        self, adapter, switch, bridged, nic0
    ):
        NETBASE = {
            NETWORK1_NAME: {
                'bonding': BOND_NAME,
                'bridged': False,
                'switch': switch,
            }
        }
        BONDBASE = {BOND_NAME: {'nics': [nic0], 'switch': switch}}

        with adapter.setupNetworks(NETBASE, BONDBASE, NOCHK):
            with nftestlib.monitor_stable_link_state(BOND_NAME):
                NETVLAN = {
                    NETWORK2_NAME: {
                        'bonding': BOND_NAME,
                        'bridged': bridged,
                        'vlan': VLAN1,
                        'switch': switch,
                    }
                }
                with adapter.setupNetworks(NETVLAN, {}, NOCHK):
                    adapter.assertNetwork(
                        NETWORK1_NAME, NETBASE[NETWORK1_NAME]
                    )
                    adapter.assertNetwork(
                        NETWORK2_NAME, NETVLAN[NETWORK2_NAME]
                    )

    @pytest.mark.xfail(
        reason='Unstable on oVirt CI',
        strict=False,
        condition=running_on_ovirt_ci(),
    )
    def test_add_net_on_existing_external_bond_preserving_mac(
        self, adapter, switch, nic0, nic1
    ):
        bond = bond_without_remove(slaves=[nic0, nic1])
        Interface.from_existing_dev_name(bond).set_mac_address(HWADDRESS)
        NETBASE = {
            NETWORK1_NAME: {
                'bonding': bond,
                'bridged': False,
                'switch': switch,
            }
        }
        with adapter.setupNetworks(NETBASE, {}, NOCHK):
            adapter.assertNetwork(NETWORK1_NAME, NETBASE[NETWORK1_NAME])
            adapter.assertBond(
                bond,
                {'nics': [nic0, nic1], 'hwaddr': HWADDRESS, 'switch': switch},
            )
        adapter.setupNetworks({}, {bond: {'remove': True}}, NOCHK)


@pytest.mark.legacy_switch
class TestReuseBondOnLegacySwitch(object):
    def test_add_net_on_existing_external_vlanned_bond(
        self, adapter, nic0, nic1
    ):
        ADDRESS1 = '192.168.99.1'
        ADDRESS2 = '192.168.99.254'
        PREFIX = '29'
        bond = bond_without_remove(slaves=[nic0, nic1])
        with vlan_device(bond) as vlan:
            # Make slaves dirty intentionally and check if they recover
            self._set_ip_address(nic0, '1.1.1.1', PREFIX)
            self._set_ip_address(nic1, '1.1.1.2', PREFIX)

            self._set_ip_address(bond, ADDRESS1, PREFIX)
            self._set_ip_address(vlan, ADDRESS2, PREFIX)

            NETBASE = {
                NETWORK1_NAME: {
                    'bonding': bond,
                    'bridged': True,
                    'ipaddr': ADDRESS1,
                    'prefix': PREFIX,
                    'switch': 'legacy',
                }
            }
            with adapter.setupNetworks(NETBASE, {}, NOCHK):
                adapter.assertNetwork(NETWORK1_NAME, NETBASE[NETWORK1_NAME])
                adapter.assertBond(
                    bond, {'nics': [nic0, nic1], 'switch': 'legacy'}
                )

                nic1_info = adapter.netinfo.nics[nic0]
                nic2_info = adapter.netinfo.nics[nic1]
                vlan_info = adapter.netinfo.vlans[vlan]
                assert nic1_info['ipv4addrs'] == []
                assert nic2_info['ipv4addrs'] == []
                assert vlan_info['ipv4addrs'] == [ADDRESS2 + '/' + PREFIX]
        adapter.setupNetworks({}, {bond: {'remove': True}}, NOCHK)

    def test_add_vlan_network_on_existing_external_bond_with_used_slave(
        self, adapter, bond0_with_slaves
    ):
        bond, (nic0, _) = bond0_with_slaves
        with vlan_device(nic0):
            NETBASE = {
                NETWORK1_NAME: {
                    'bonding': bond,
                    'bridged': True,
                    'switch': 'legacy',
                    'vlan': 17,
                }
            }

            with pytest.raises(SetupNetworksError) as err:
                with adapter.setupNetworks(NETBASE, {}, NOCHK):
                    pass

            assert err.value.status == ne.ERR_USED_NIC
            assert 'Nics with multiple usages' in err.value.msg

    def _set_ip_address(self, iface, addr, prefixlen):
        Interface.from_existing_dev_name(iface).add_ip(
            addr, prefixlen, IpFamily.IPv4
        )
