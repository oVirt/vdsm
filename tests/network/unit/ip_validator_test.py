# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

import pytest

from vdsm.network import errors as ne
from vdsm.network.ip import validator


class TestIPNameserverValidator(object):
    def test_ignore_remove_networks(self):
        validator.validate(
            {
                'NET0': {
                    'remove': True,
                    'defaultRoute': False,
                    'nameservers': ['8.8.8.8'],
                }
            }
        )

    def test_nameserver_defined_on_a_non_primary_network_fails(self):
        with pytest.raises(ne.ConfigNetworkError) as cne:
            validator.validate(
                {'NET0': {'defaultRoute': False, 'nameservers': ['8.8.8.8']}}
            )
        assert cne.value.errCode == ne.ERR_BAD_PARAMS

    def test_nameserver_faulty_ipv4_address(self):
        with pytest.raises(ne.ConfigNetworkError) as cne:
            validator.validate(
                {'NET0': {'defaultRoute': True, 'nameservers': ['a.8.8.8']}}
            )
        assert cne.value.errCode == ne.ERR_BAD_ADDR

    def test_nameserver_faulty_ipv6_address(self):
        with pytest.raises(ne.ConfigNetworkError) as cne:
            validator.validate(
                {
                    'NET0': {
                        'defaultRoute': True,
                        'nameservers': ['2001:bla::1'],
                    }
                }
            )
        assert cne.value.errCode == ne.ERR_BAD_ADDR

    def test_nameserver_valid_ipv4_address(self):
        validator.validate(
            {'NET0': {'defaultRoute': True, 'nameservers': ['8.8.8.8']}}
        )

    def test_nameserver_valid_ipv6_address(self):
        validator.validate(
            {'NET0': {'defaultRoute': True, 'nameservers': ['2001::1']}}
        )

    def test_nameserver_address_with_zone_identifier(self):
        validator.validate(
            {'NET0': {'defaultRoute': True, 'nameservers': ['fe80::1%eth1']}}
        )


class TestStaticIpv4ConfigValidator(object):
    def test_ip_address_without_netmask_fails(self):
        self._test_ip_config_fails(ipaddr='10.10.10.10')

    def test_gateway_without_ip_address_fails(self):
        self._test_ip_config_fails(gateway='10.10.10.1')

    def test_netmask_without_ip_address_fails(self):
        self._test_ip_config_fails(netmask='255.255.255.0')

    def test_invalid_ip_address_fails(self):
        self._test_ip_config_fails(
            ipaddr='10.10.10.10.10', netmask='255.255.255.0'
        )

    def test_invalid_netmask_fails(self):
        self._test_ip_config_fails(
            ipaddr='10.10.10.10', netmask='355.255.255.0'
        )

    def test_invalid_gateway_fails(self):
        self._test_ip_config_fails(
            ipaddr='10.10.10.10', netmask='255.255.255.0', gateway='abcdef'
        )

    def test_static_and_dhcp_mix_fails(self):
        self._test_ip_config_fails(
            ipaddr='10.10.10.10', netmask='255.255.255.0', bootproto='dhcp'
        )

    def test_config_without_gateway(self):
        STATIC_CONFIG = dict(ipaddr='10.10.10.10', netmask='255.255.255.0')
        validator.validate_static_ipv4_config(STATIC_CONFIG)

    def test_config_with_gateway(self):
        STATIC_CONFIG = dict(
            ipaddr='10.10.10.10', netmask='255.255.255.0', gateway='10.10.10.1'
        )
        validator.validate_static_ipv4_config(STATIC_CONFIG)

    def _test_ip_config_fails(self, **setup):
        with pytest.raises(ne.ConfigNetworkError) as cne:
            validator.validate_static_ipv4_config(setup)
        assert cne.value.errCode == ne.ERR_BAD_ADDR
