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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#
from __future__ import absolute_import

from nose.plugins.attrib import attr

from testlib import VdsmTestCase, mock, namedTemporaryDir

from vdsm.network import netconfpersistence as netconf
from vdsm.network import netupgrade


@attr(type='unit')
@mock.patch.object(
    netupgrade, 'LEGACY_MANAGEMENT_NETWORKS', ('ovirtmgmt', 'rhevm'))
@mock.patch.object(netupgrade, 'PersistentConfig')
@mock.patch.object(netupgrade, 'RunningConfig')
class NetUpgradeUnifiedConfigTest(VdsmTestCase):

    def test_old_config_with_no_networks(self, mockRConfig, mockPConfig):
        RAW_CONFIG = {}
        NORMALIZED_CONFIG = {}

        self._assert_upgrade_config(
            RAW_CONFIG, NORMALIZED_CONFIG,
            mockRConfig.return_value, mockPConfig.return_value)

    def test_old_config_with_ifcfg_keys(self, mockRConfig, mockPConfig):
        RAW_CONFIG = {'net0': {'nic': 'eth0',
                               'defaultRoute': False,
                               'UNSUPPORTED_KEY0': 'n/a'}}
        NORMALIZED_CONFIG = {'net0': {'nic': 'eth0',
                                      'defaultRoute': False}}

        self._assert_upgrade_config(
            RAW_CONFIG, NORMALIZED_CONFIG,
            mockRConfig.return_value, mockPConfig.return_value)

    def test_old_config_with_non_mgmt_net_and_missing_droute(
            self, mockRConfig, mockPConfig):
        RAW_CONFIG = {'net0': {'nic': 'eth0'}}
        NORMALIZED_CONFIG = {'net0': {'nic': 'eth0',
                                      'defaultRoute': False}}

        self._assert_upgrade_config(
            RAW_CONFIG, NORMALIZED_CONFIG,
            mockRConfig.return_value, mockPConfig.return_value)

    def test_old_config_with_mgmt_net_and_missing_droute(
            self, mockRConfig, mockPConfig):
        RAW_CONFIG = {'ovirtmgmt': {'nic': 'eth0'}}
        NORMALIZED_CONFIG = {'ovirtmgmt': {'nic': 'eth0',
                                           'defaultRoute': True}}

        self._assert_upgrade_config(
            RAW_CONFIG, NORMALIZED_CONFIG,
            mockRConfig.return_value, mockPConfig.return_value)

    def _assert_upgrade_config(
            self, raw_config, normalized_config, rconfig, pconfig):
        rconfig.networks = raw_config
        pconfig.networks = raw_config

        netupgrade.upgrade()

        self.assertEqual(normalized_config, rconfig.networks)
        self.assertEqual(normalized_config, pconfig.networks)
        if normalized_config:
            rconfig.save.assert_called_once_with()
            pconfig.save.assert_called_once_with()


@attr(type='integration')
@mock.patch.object(
    netupgrade, 'LEGACY_MANAGEMENT_NETWORKS', ('ovirtmgmt', 'rhevm'))
class NetUpgradeVolatileRunConfig(VdsmTestCase):

    def test_upgrade_volatile_running_config(self):
        with namedTemporaryDir() as pdir, namedTemporaryDir() as vdir:
            with mock.patch.object(netconf, 'CONF_RUN_DIR', pdir),\
                    mock.patch.object(netconf, 'CONF_VOLATILE_RUN_DIR', vdir):

                vol_rconfig = netconf.RunningConfig(volatile=True)
                vol_rconfig.save()

                netupgrade.upgrade()

                pers_rconfig = netconf.RunningConfig()
                self.assertFalse(vol_rconfig.config_exists())
                self.assertTrue(pers_rconfig.config_exists())
