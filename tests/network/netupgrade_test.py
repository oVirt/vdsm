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
@mock.patch.object(netupgrade.libvirtnetwork, 'networks', lambda: ())
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
        NORMALIZED_CONFIG = {'net0': DEFAULT_NET_ATTRS}

        self._assert_upgrade_config(
            RAW_CONFIG, NORMALIZED_CONFIG,
            mockRConfig.return_value, mockPConfig.return_value)

    def test_old_config_with_non_mgmt_net_and_missing_droute(
            self, mockRConfig, mockPConfig):
        RAW_CONFIG = {'net0': {'nic': 'eth0'}}
        NORMALIZED_CONFIG = {'net0': DEFAULT_NET_ATTRS}

        self._assert_upgrade_config(
            RAW_CONFIG, NORMALIZED_CONFIG,
            mockRConfig.return_value, mockPConfig.return_value)

    @mock.patch('vdsm.network.canonicalize.dns.get_host_nameservers',
                lambda: [])
    def test_old_config_with_mgmt_net_and_missing_droute(
            self, mockRConfig, mockPConfig):
        RAW_CONFIG = {'ovirtmgmt': {'nic': 'eth0'}}
        net_attrs = dict(DEFAULT_NET_ATTRS, defaultRoute=True)
        NORMALIZED_CONFIG = {'ovirtmgmt': net_attrs}

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


@attr(type='unit')
@mock.patch.object(netupgrade, 'netinfo', lambda x: None)
@mock.patch.object(netupgrade, 'NetInfo', lambda x: None)
@mock.patch.object(netupgrade, 'libvirt_vdsm_nets', lambda x: None)
@mock.patch.object(netupgrade.libvirtnetwork, 'networks', lambda: ())
@mock.patch.object(netupgrade.config, 'get', lambda a, b: 'ifcfg')
@mock.patch.object(netupgrade.Ifcfg, 'owned_device', return_value=True)
@mock.patch.object(netupgrade, 'KernelConfig')
@mock.patch.object(netupgrade, 'PersistentConfig')
@mock.patch.object(netupgrade, 'RunningConfig')
class NetCreateUnifiedConfigTest(VdsmTestCase):

    def test_create_unified_config(
            self, mockRConfig, mockPConfig, mockKConfig, mock_owned_device):
        rconfig = mockRConfig.return_value
        pconfig = mockPConfig.return_value
        kconfig = mockKConfig.return_value
        self._setup_missing_unified_config(kconfig, pconfig, rconfig)

        netupgrade.upgrade()

        self._assert_unified_config_created(kconfig, rconfig, mockRConfig)

    def _setup_missing_unified_config(self, kconfig, pconfig, rconfig):
        kconfig.networks = {'netname': {}}
        kconfig.bonds = {'bondname': {}}
        rconfig.config_exists.return_value = False
        pconfig.config_exists.return_value = False

    def _assert_unified_config_created(self, kconfig, rconfig, mockRConfig):
        self.assertEqual(kconfig.networks, rconfig.networks)
        self.assertEqual(kconfig.bonds, rconfig.bonds)

        rconfig.save.assert_called_once_with()
        mockRConfig.store.assert_called_once_with()


@attr(type='integration')
@mock.patch.object(netupgrade.libvirtnetwork, 'networks', lambda: ())
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


DEFAULT_NET_ATTRS = {'bootproto': 'none',
                     'bridged': True,
                     'defaultRoute': False,
                     'dhcpv6': False,
                     'ipv6autoconf': False,
                     'mtu': 1500,
                     'nameservers': [],
                     'nic': 'eth0',
                     'stp': False,
                     'switch': 'legacy'}
