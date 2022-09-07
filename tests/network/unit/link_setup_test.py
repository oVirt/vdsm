# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

from vdsm.network.link import setup as linksetup


def test_parse_nets_bridge_opts():
    nets = {
        'br1': {
            'custom': {
                'bridge_opts': 'multicast_router=0 multicast_snooping=0'
            }
        },
        'br2': {
            'custom': {
                'bridge_opts': 'multicast_router=1 multicast_snooping=1'
            }
        },
    }
    expected = {
        'br1': {'multicast_router': '0', 'multicast_snooping': '0'},
        'br2': {'multicast_router': '1', 'multicast_snooping': '1'},
    }

    for name, opts in linksetup.parse_nets_bridge_opts(nets):
        assert expected[name] == opts
