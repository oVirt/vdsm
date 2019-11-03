#
# Copyright 2017 Red Hat, Inc.
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

import copy

import six

from network.compat import mock
from testlib import VdsmTestCase

from vdsm.network import canonicalize
from vdsm.network import errors as ne


NET0_SETUP = {'NET0': {'nic': 'eth0', 'switch': 'legacy'}}
NET1_SETUP = {'NET1': {'nic': 'eth0', 'switch': 'legacy'}}
NET2_SETUP = {'NET2': {'nic': 'eth0', 'switch': 'legacy'}}


@mock.patch.object(canonicalize, 'RunningConfig')
class TestDefaultRouteCanonicalization(VdsmTestCase):
    def test_request_one_defroute_no_existing_defroute(self, mockRConfig):
        running_config = self._nets_config(NET1_SETUP, default_route=False)
        requested_nets = self._nets_config(NET0_SETUP, default_route=True)
        original_requested_nets = copy.deepcopy(requested_nets)

        mockRConfig.return_value.networks = running_config

        canonicalize.canonicalize_networks(requested_nets)

        self._assert_default_route_keys(
            original_requested_nets, requested_nets
        )

    def test_request_one_defroute_no_existing_defroute_key(self, mockRConfig):
        running_config = self._nets_config(NET1_SETUP, default_route=None)
        requested_nets = self._nets_config(NET0_SETUP, default_route=True)
        original_requested_nets = copy.deepcopy(requested_nets)

        mockRConfig.return_value.networks = running_config

        canonicalize.canonicalize_networks(requested_nets)

        self._assert_default_route_keys(
            original_requested_nets, requested_nets
        )

    def test_request_no_defroute_no_existing_defroute(self, mockRConfig):
        running_config = self._nets_config(NET1_SETUP, default_route=False)
        requested_nets = self._nets_config(NET0_SETUP, default_route=False)
        original_requested_nets = copy.deepcopy(requested_nets)

        mockRConfig.return_value.networks = running_config

        canonicalize.canonicalize_networks(requested_nets)

        self._assert_default_route_keys(
            original_requested_nets, requested_nets
        )

    def test_request_one_defroute_existing_same_defroute(self, mockRConfig):
        running_config = self._nets_config(NET0_SETUP, default_route=True)
        requested_nets = self._nets_config(NET0_SETUP, default_route=True)
        original_requested_nets = copy.deepcopy(requested_nets)

        mockRConfig.return_value.networks = running_config

        canonicalize.canonicalize_networks(requested_nets)

        self._assert_default_route_keys(
            original_requested_nets, requested_nets
        )

    def test_request_one_defroute_removing_existing_different_defroute(
        self, mockRConfig
    ):
        running_config = self._nets_config(NET1_SETUP, default_route=True)
        requested_nets = _merge_dicts(
            self._nets_config(NET0_SETUP, default_route=True),
            self._nets_config(NET1_SETUP, default_route=False),
        )
        original_requested_nets = copy.deepcopy(requested_nets)

        mockRConfig.return_value.networks = running_config

        canonicalize.canonicalize_networks(requested_nets)

        self._assert_default_route_keys(
            original_requested_nets, requested_nets
        )

    def test_request_multi_defroute(self, mockRConfig):
        mockRConfig.return_value.networks = {}
        nets_base_setup = _merge_dicts(NET0_SETUP, NET1_SETUP)
        requested_nets = self._nets_config(nets_base_setup, default_route=True)

        with self.assertRaises(ne.ConfigNetworkError):
            canonicalize.canonicalize_networks(requested_nets)

    def test_request_one_defroute_existing_different_defroute(
        self, mockRConfig
    ):
        running_config = self._nets_config(NET1_SETUP, default_route=True)
        requested_nets = self._nets_config(NET0_SETUP, default_route=True)
        original_requested_nets = copy.deepcopy(requested_nets)

        mockRConfig.return_value.networks = running_config

        canonicalize.canonicalize_networks(requested_nets)

        auto_generated_net = self._nets_config(NET1_SETUP, default_route=False)
        expected_canonicalized_request = _merge_dicts(
            auto_generated_net, original_requested_nets
        )

        self._assert_default_route_keys(
            expected_canonicalized_request, requested_nets
        )

    def test_request_multi_defroute_removing_existing_different_defroute(
        self, mockRConfig
    ):
        running_config = self._nets_config(NET2_SETUP, default_route=True)
        requested_nets = _merge_dicts(
            self._nets_config(NET0_SETUP, default_route=True),
            self._nets_config(NET1_SETUP, default_route=True),
            self._nets_config(NET2_SETUP, default_route=False),
        )

        mockRConfig.return_value.networks = running_config

        with self.assertRaises(ne.ConfigNetworkError):
            canonicalize.canonicalize_networks(requested_nets)

    def _nets_config(self, nets_config, default_route):
        config = copy.deepcopy(nets_config)
        for net_attrs in six.itervalues(config):
            if default_route is not None:
                net_attrs['defaultRoute'] = default_route

        return config

    def _assert_default_route_keys(self, expected_setup, actual_setup):
        self.assertEqual(set(expected_setup), set(actual_setup))
        for net in expected_setup:
            self.assertEqual(
                expected_setup[net]['defaultRoute'],
                actual_setup[net]['defaultRoute'],
                '{} != {}'.format(
                    {net: expected_setup[net]}, {net: actual_setup[net]}
                ),
            )


def _merge_dicts(*dicts):
    merged_dicts = {}
    for d in dicts:
        merged_dicts.update(d)
    return merged_dicts
