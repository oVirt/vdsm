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

from nose.plugins.attrib import attr

from vdsm.network import errors as ne

from .netfunctestlib import NetFuncTestCase, NOCHK, SetupNetworksError


NETWORK_NAME = 'test-network'


class IpConfigValidationTemplate(NetFuncTestCase):

    __test__ = False

    def test_invalid_ip_config_missing_addresses(self):
        self._test_invalid_ip_config_fails(ipaddr='1.2.3.4')
        self._test_invalid_ip_config_fails(netmask='1.2.3.4')
        self._test_invalid_ip_config_fails(gateway='1.2.3.4')

    def test_invalid_ip_config_out_of_range_addresses(self):
        self._test_invalid_ip_config_fails(
            ipaddr='1.2.3.256', netmask='255.255.0.0')
        self._test_invalid_ip_config_fails(
            ipaddr='1.2.3.4', netmask='256.255.0.0')
        self._test_invalid_ip_config_fails(
            ipaddr='1.2.3.4', netmask='255.255.0.0', gateway='1.2.3.256')

    def test_invalid_ip_config_bad_format_addresses(self):
        self._test_invalid_ip_config_fails(
            ipaddr='1.2.3.4.5', netmask='255.255.0.0')
        self._test_invalid_ip_config_fails(
            ipaddr='1.2.3', netmask='255.255.0.0')

    def _test_invalid_ip_config_fails(self, **ip_config):
        ip_config.update(switch=self.switch)
        with self.assertRaises(SetupNetworksError) as err:
            with self.setupNetworks({NETWORK_NAME: ip_config}, {}, NOCHK):
                pass
        self.assertEqual(err.exception.status, ne.ERR_BAD_ADDR)


@attr(type='functional', switch='legacy')
class IpConfigValidationLegacyTest(IpConfigValidationTemplate):

    __test__ = True
    switch = 'legacy'


@attr(type='functional', switch='ovs')
class IpConfigValidationOvsTest(IpConfigValidationTemplate):

    __test__ = True
    switch = 'ovs'
