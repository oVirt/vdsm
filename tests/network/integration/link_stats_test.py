# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

from contextlib import contextmanager

import pytest

from network.nettestlib import bond_device
from network.nettestlib import bridge_device
from network.nettestlib import dummy_device
from network.nettestlib import vlan_device

from vdsm.network.link import stats as link_stats


@contextmanager
def _bond_device_master(slaves):
    with bond_device(slaves) as bond:
        yield bond


@contextmanager
def _vlan_device():
    with dummy_device() as nic:
        with vlan_device(nic, 101) as vlan:
            yield vlan


@contextmanager
def _bridge_device():
    with bridge_device() as bridge:
        yield bridge


@pytest.mark.parametrize(
    'device_ctx, device_ctx_args',
    [
        (dummy_device, {}),
        (_bond_device_master, {'slaves': ()}),
        (_vlan_device, {}),
        (_bridge_device, {}),
    ],
    ids=['nic', 'bond', 'vlan', 'bridge'],
)
def test_report(device_ctx, device_ctx_args):
    with device_ctx(**device_ctx_args) as dev:
        stats = link_stats.report()
        assert dev in stats
        expected_stat_names = {
            'name',
            'rx',
            'tx',
            'state',
            'rxDropped',
            'txDropped',
            'rxErrors',
            'txErrors',
            'speed',
            'duplex',
        }
        assert expected_stat_names == set(stats[dev])
