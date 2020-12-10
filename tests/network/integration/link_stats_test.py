# Copyright 2019-2020 Red Hat, Inc.
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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#
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
