# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

from vdsm.network.netinfo import qos
from vdsm.network.tc import cls
from vdsm.network.netinfo.cache import add_qos_info_to_devices

HOST_QOS_CONFIG1 = {
    'out': {
        'ls': {'m1': 4000000, 'd': 100000, 'm2': 3000000},
        'ul': {'m1': 0, 'd': 0, 'm2': 8000000},
    }
}
HOST_QOS_CONFIG2 = {'out': {'ls': {'m2': 25000000}}}

NETWORK1_NAME = 'test-network1'
NETWORK2_NAME = 'test-network2'
VLAN1 = 10
VLAN2 = 20

NIC = 'dummy_nic'
BOND = 'dummy_bond'


class TestConversions(object):
    def test_qos_to_str(self):
        data = (
            (
                {
                    'ls': {'m1': 100, 'd': 10, 'm2': 300},
                    'ul': {'m1': 100, 'd': 10, 'm2': 300},
                    'rt': {'m1': 100, 'd': 10, 'm2': 300},
                },
                {
                    'ls': ['m1', '100bit', 'd', '10us', 'm2', '300bit'],
                    'ul': ['m1', '100bit', 'd', '10us', 'm2', '300bit'],
                    'rt': ['m1', '100bit', 'd', '10us', 'm2', '300bit'],
                },
            ),
            (
                {
                    'ls': {'m1': 100, 'd': 10, 'm2': 300},
                    'rt': {'m1': 100, 'd': 10, 'm2': 300},
                },
                {
                    'ls': ['m1', '100bit', 'd', '10us', 'm2', '300bit'],
                    'rt': ['m1', '100bit', 'd', '10us', 'm2', '300bit'],
                },
            ),
            (
                {'ls': {'m1': 100, 'd': 10, 'm2': 300}},
                {'ls': ['m1', '100bit', 'd', '10us', 'm2', '300bit']},
            ),
        )
        for input_qos, expected_str in data:
            assert cls._qos_to_str_dict(input_qos) == expected_str

    def test_get_root_qdisc(self):
        root = {
            'kind': 'hfsc',
            'root': True,
            'handle': '1:',
            'refcnt': 2,
            'hfsc': {'default': 0x5000},
        }
        qdiscs = (
            root,
            {
                'kind': 'hfsc',
                'handle': '1:10',
                'parent': '1:',
                'leaf': '10:',
                'hfsc': {'default': 0x8002},
            },
            {
                'kind': 'hfsc',
                'handle': '1:20',
                'parent': '1:',
                'leaf': '20:',
                'hfsc': {'default': 0x8002},
            },
        )
        assert qos.get_root_qdisc(qdiscs) == root


class TestAddDevicesQoSInfo(object):
    def test_no_devices_no_networks(self):
        nets_info = {}

        devices_info = _create_devices_info_dict()
        expected_devices_info = _create_devices_info_dict()

        add_qos_info_to_devices(nets_info, devices_info)

        assert devices_info == expected_devices_info

    def test_no_qos_on_nic_and_bonded_nets(self):
        nets_info = {
            NETWORK1_NAME: {'nic': NIC, 'southbound': NIC},
            NETWORK2_NAME: {'bonding': BOND, 'southbound': BOND},
        }

        devices_info = _create_devices_info_dict()
        expected_devices_info = _create_devices_info_dict()

        add_qos_info_to_devices(nets_info, devices_info)

        assert devices_info == expected_devices_info

    def test_non_vlanned_qos_on_nic_and_bonded_nets(self):
        nets_info = {
            NETWORK1_NAME: {
                'nic': NIC,
                'southbound': NIC,
                'hostQos': HOST_QOS_CONFIG1,
            },
            NETWORK2_NAME: {
                'bonding': BOND,
                'southbound': BOND,
                'hostQos': HOST_QOS_CONFIG2,
            },
        }

        devices_info = _create_devices_info_dict(
            bondings={BOND: {}}, nics={NIC: {}}
        )

        expected_devices_info = _create_devices_info_dict(
            bondings={
                BOND: {'qos': [{'hostQos': HOST_QOS_CONFIG2, 'vlan': -1}]}
            },
            nics={NIC: {'qos': [{'hostQos': HOST_QOS_CONFIG1, 'vlan': -1}]}},
        )

        add_qos_info_to_devices(nets_info, devices_info)

        assert devices_info == expected_devices_info

    def test_two_identical_qos_on_same_bond(self):
        nets_info = {
            NETWORK1_NAME: {
                'vlan': VLAN1,
                'bonding': BOND,
                'southbound': BOND + '.' + str(VLAN1),
                'hostQos': HOST_QOS_CONFIG1,
            },
            NETWORK2_NAME: {
                'bonding': BOND,
                'southbound': BOND,
                'hostQos': HOST_QOS_CONFIG1,
            },
        }

        devices_info = _create_devices_info_dict(
            vlans={BOND + '.' + str(VLAN1): {'iface': BOND}},
            bondings={BOND: {}},
        )

        expected_devices_info = _create_devices_info_dict(
            vlans=devices_info['vlans'],
            bondings={
                BOND: {
                    'qos': [
                        {'hostQos': HOST_QOS_CONFIG1, 'vlan': -1},
                        {'hostQos': HOST_QOS_CONFIG1, 'vlan': VLAN1},
                    ]
                }
            },
        )

        add_qos_info_to_devices(nets_info, devices_info)

        assert devices_info == expected_devices_info

    def test_vlanned_nets_with_qos(self):
        nets_info = {
            NETWORK1_NAME: {
                'nic': NIC,
                'vlan': VLAN1,
                'southbound': NIC + '.' + str(VLAN1),
                'hostQos': HOST_QOS_CONFIG1,
            },
            NETWORK2_NAME: {
                'bonding': BOND,
                'vlan': VLAN2,
                'southbound': BOND + '.' + str(VLAN2),
                'hostQos': HOST_QOS_CONFIG2,
            },
        }

        devices_info = _create_devices_info_dict(
            vlans={
                NIC + '.' + str(VLAN1): {'iface': NIC},
                BOND + '.' + str(VLAN2): {'iface': BOND},
            },
            bondings={BOND: {}},
            nics={NIC: {}},
        )

        expected_devices_info = _create_devices_info_dict(
            vlans=devices_info['vlans'],
            bondings={
                BOND: {'qos': [{'hostQos': HOST_QOS_CONFIG2, 'vlan': VLAN2}]}
            },
            nics={
                NIC: {'qos': [{'hostQos': HOST_QOS_CONFIG1, 'vlan': VLAN1}]}
            },
        )

        add_qos_info_to_devices(nets_info, devices_info)

        assert devices_info == expected_devices_info


def _create_devices_info_dict(
    bridges=None, vlans=None, bondings=None, nics=None
):
    return {
        'bridges': bridges or {},
        'vlans': vlans or {},
        'bondings': bondings or {},
        'nics': nics or {},
    }
