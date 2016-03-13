#
# Copyright 2013 Red Hat, Inc.
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
import copy
import errno
import json
import logging
import netaddr
import os
import pwd
import six
import string

from .config import config
from .tool.restore_nets import restore
from . import constants
from . import utils

CONF_RUN_DIR = constants.P_VDSM_RUN + 'netconf/'
# The persistent path is inside of an extra "persistence" dir in order to get
# oVirt Node to persist the symbolic links that are necessary for the
# atomic storage of running config into persistent config.
CONF_PERSIST_DIR = constants.P_VDSM_LIB + 'persistence/netconf/'
_BONDING_MODES = {
    # TODO: this dictionary and the reverse mapping are duplicated in code
    '0': 'balance-rr', '1': 'active-backup', '2': 'balance-xor',
    '3': 'broadcast', '4': '802.3ad', '5': 'balance-tlb', '6': 'balance-alb'
}
_BONDING_MODES_REVERSED = dict((v, k) for k, v in _BONDING_MODES.iteritems())


class BaseConfig(object):
    def __init__(self, networks, bonds):
        self.networks = networks
        self.bonds = bonds

    def setNetwork(self, network, attributes):
        # Clean netAttrs from fields that should not be serialized
        cleanAttrs = dict((key, value) for key, value in attributes.iteritems()
                          if value is not None and key not in
                          ('configurator', '_netinfo', 'force',
                           'bondingOptions', 'implicitBonding'))
        self.networks[network] = cleanAttrs
        logging.info('Adding network %s(%s)', network, cleanAttrs)

    def removeNetwork(self, network):
        try:
            del self.networks[network]
            logging.info('Removing network %s', network)
        except KeyError:
            logging.debug('Network %s not found for removal', network)

    def setBonding(self, bonding, attributes):
        self.bonds[bonding] = attributes
        logging.info('Adding %s(%s)', bonding, attributes)

    def removeBonding(self, bonding):
        try:
            del self.bonds[bonding]
            logging.info('Removing %s', bonding)
        except KeyError:
            logging.debug('%s not found for removal', bonding)

    def diffFrom(self, other):
        """Returns a diff Config that shows the what should be changed for
        going from other to self."""
        diff = BaseConfig(self._confDictDiff(self.networks, other.networks),
                          self._confDictDiff(self.bonds, other.bonds))
        return diff

    def __eq__(self, other):
        return self.networks == other.networks and self.bonds == other.bonds

    def __repr__(self):
        return '%s(%s, %s)' % (self.__class__.__name__, self.networks,
                               self.bonds)

    def __nonzero__(self):
        return True if self.networks or self.bonds else False

    @staticmethod
    def _confDictDiff(lhs, rhs):
        result = {}
        for name in rhs:
            if name not in lhs:
                result[name] = {'remove': True}

        for name, attr in lhs.iteritems():
            if name not in rhs or attr != rhs[name]:
                result[name] = lhs[name]
        return result


class Config(BaseConfig):
    def __init__(self, savePath):
        self.networksPath = os.path.join(savePath, 'nets', '')
        self.bondingsPath = os.path.join(savePath, 'bonds', '')
        super(Config, self).__init__(self._getConfigs(self.networksPath),
                                     self._getConfigs(self.bondingsPath))

    def delete(self):
        self.networks = {}
        self.bonds = {}
        self._clearDisk()

    def save(self):
        self._clearDisk()
        for bond, attrs in self.bonds.iteritems():
            self._setConfig(attrs, self._bondingPath(bond))
        for network, attrs in self.networks.iteritems():
            self._setConfig(attrs, self._networkPath(network))
        logging.info('Saved new config %r to %s and %s' %
                     (self, self.networksPath, self.bondingsPath))

    def _networkPath(self, network):
        return self.networksPath + network

    def _bondingPath(self, bonding):
        return self.bondingsPath + bonding

    @staticmethod
    def _getConfigDict(path):
        try:
            with open(path, 'r') as configurationFile:
                return json.load(configurationFile)
        except IOError as ioe:
            if ioe.errno == os.errno.ENOENT:
                logging.debug('Network entity at %s not found', path)
                return {}
            else:
                raise

    def _getConfigs(self, path):
        if not os.path.exists(path):
            return {}

        networkEntities = {}

        for fileName in os.listdir(path):
            fullPath = path + fileName
            networkEntities[fileName] = self._getConfigDict(fullPath)

        return networkEntities

    @staticmethod
    def _setConfig(config, path):
        dirPath = os.path.dirname(path)
        try:
            os.makedirs(dirPath)
        except OSError as ose:
            if errno.EEXIST != ose.errno:
                raise
        with open(path, 'w') as configurationFile:
            json.dump(config, configurationFile, indent=4)

        # Set owner to vdsm (required by ovirt-node)
        vdsm_uid = pwd.getpwnam(constants.VDSM_USER).pw_uid
        os.chown(os.path.dirname(dirPath), vdsm_uid, -1)
        os.chown(dirPath, vdsm_uid, -1)
        os.chown(path, vdsm_uid, -1)

    @staticmethod
    def _removeConfig(path):
        utils.rmFile(path)

    def _clearDisk(self):
        try:
            logging.info('Clearing %s and %s', self.networksPath,
                         self.bondingsPath)
            for filePath in os.listdir(self.networksPath):
                self._removeConfig(self.networksPath + filePath)

            for filePath in os.listdir(self.bondingsPath):
                self._removeConfig(self.bondingsPath + filePath)
        except OSError as ose:
            if ose.errno == errno.ENOENT:
                logging.debug('No existent config to clear.')
            else:
                raise


class KernelConfig(BaseConfig):
    def __init__(self, netinfo):
        super(KernelConfig, self).__init__({}, {})
        self._netinfo = netinfo
        for net, net_attr in self._analyze_netinfo_nets(netinfo):
            self.setNetwork(net, net_attr)
        for bond, bond_attr in self._analyze_netinfo_bonds(netinfo):
            self.setBonding(bond, bond_attr)

    def __eq__(self, other):
        normalized_other = self.normalize(other)
        return (self.networks == normalized_other.networks
                and self.bonds == normalized_other.bonds)

    def _analyze_netinfo_nets(self, netinfo):
        for net, net_attr in netinfo.networks.iteritems():
            yield net, self._translate_netinfo_net(net, net_attr)

    def _analyze_netinfo_bonds(self, netinfo):
        for bond, bond_attr in netinfo.bondings.iteritems():
            yield bond, self._translate_netinfo_bond(bond_attr)

    def _translate_netinfo_net(self, net, net_attr):
        nics, _, vlan_id, bond = \
            self._netinfo.getNicsVlanAndBondingForNetwork(net)
        attributes = {}
        self._translate_bridged(attributes, net_attr)
        self._translate_mtu(attributes, net_attr)
        self._translate_vlan(attributes, vlan_id)
        if bond:
            self._translate_bonding(attributes, bond)
        elif nics:
            self._translate_nics(attributes, nics)
        self._translate_ipaddr(attributes, net_attr)
        self._translate_hostqos(attributes, net_attr)

        return attributes

    def _translate_hostqos(self, attributes, net_attr):
        if net_attr.get('hostQos'):
            attributes['hostQos'] = self._remove_zero_values_in_net_qos(
                net_attr['hostQos'])

    def _translate_ipaddr(self, attributes, net_attr):
        attributes['bootproto'] = 'dhcp' if net_attr['dhcpv4'] else 'none'
        attributes['dhcpv6'] = net_attr['dhcpv6']
        ifcfg = net_attr.get('cfg')
        # TODO: we must not depend on 'cfg', which is configurator-dependent.
        # TODO: Look up in the routing table instead.
        if ifcfg and ifcfg.get('DEFROUTE') == 'yes':
            attributes['defaultRoute'] = True
        else:
            attributes['defaultRoute'] = False
        # only static addresses are part of {Persistent,Running}Config.
        if attributes['bootproto'] == 'none':
            if net_attr['addr']:
                attributes['ipaddr'] = net_attr['addr']
            if net_attr['netmask']:
                attributes['netmask'] = net_attr['netmask']
            if net_attr['gateway']:
                attributes['gateway'] = net_attr['gateway']
        # IPv6 is ignored to avoid needless reconfiguration of networks

    def _translate_ipv6_addr(self, ipv6_addrs):
        return [
            addr for addr in ipv6_addrs
            if not netaddr.IPAddress(addr.split('/')[0]).is_link_local()]

    def _translate_nics(self, attributes, nics):
        nic, = nics
        attributes['nic'] = nic

    def _translate_bonding(self, attributes, bond):
        attributes['bonding'] = bond

    def _translate_vlan(self, attributes, vlan):
        if vlan is not None:
            attributes['vlan'] = str(vlan)

    def _translate_mtu(self, attributes, net_attr):
        attributes['mtu'] = net_attr['mtu']

    def _translate_bridged(self, attributes, net_attr):
        attributes['bridged'] = net_attr['bridged']
        if net_attr['bridged']:
            attributes['stp'] = self._netinfo.stpBooleanize(net_attr['stp'])

    def _translate_netinfo_bond(self, bond_attr):
        return {
            'nics': sorted(bond_attr['slaves']),
            'options': self._netinfo.bondOptsForIfcfg(bond_attr['opts'])
        }

    def _remove_zero_values_in_net_qos(self, net_qos):
        """
        net_qos = {'out': {
                'ul': {'m1': 0, 'd': 0, 'm2': 8000000},
                'ls': {'m1': 4000000, 'd': 100000, 'm2': 3000000}}}
        stripped_qos = {'out': {
                'ul': {'m2': 8000000},
                'ls': {'m1': 4000000, 'd': 100000, 'm2': 3000000}}}"""
        stripped_qos = {}
        for part, part_config in net_qos.iteritems():
            stripped_qos[part] = dict(part_config)  # copy
            for curve, curve_config in part_config.iteritems():
                stripped_qos[part][curve] = dict((k, v) for k, v
                                                 in curve_config.iteritems()
                                                 if v != 0)
        return stripped_qos

    def normalize(self, running_config):
        # TODO: normalize* methods can become class functions, as they are only
        # TODO: dependent in self._netinfo, which is only needed to access
        # TODO: netinfo module level functions, that cannot be imported here
        # TODO: because of a circular import.
        config_copy = copy.deepcopy(running_config)

        self._normalize_bridge(config_copy)
        self._normalize_vlan(config_copy)
        self._normalize_mtu(config_copy)
        self._normalize_blockingdhcp(config_copy)
        self._normalize_dhcp(config_copy)
        self._normalize_bonding_opts(config_copy)
        self._normalize_bonding_nics(config_copy)
        self._normalize_address(config_copy)
        self._normalize_ifcfg_keys(config_copy)

        return config_copy

    def _normalize_vlan(self, config_copy):
        for net_attr in config_copy.networks.itervalues():
            if 'vlan' in net_attr:
                net_attr['vlan'] = str(net_attr['vlan'])

    def _normalize_bridge(self, config_copy):
        for net_attr in config_copy.networks.itervalues():
            if utils.tobool(net_attr.get('bridged', True)):
                net_attr['bridged'] = True
                self._normalize_stp(net_attr)
            else:
                net_attr['bridged'] = False

    def _normalize_stp(self, net_attr):
        stp = net_attr.pop('stp', net_attr.pop('STP', None))
        net_attr['stp'] = self._netinfo.stpBooleanize(
            stp)

    def _normalize_mtu(self, config_copy):
        for net_attr in config_copy.networks.itervalues():
            if 'mtu' in net_attr:
                net_attr['mtu'] = str(net_attr['mtu'])
            else:
                net_attr['mtu'] = self._netinfo.getDefaultMtu()

    def _normalize_blockingdhcp(self, config_copy):
        for net_attr in config_copy.networks.itervalues():
            if 'blockingdhcp' in net_attr:
                net_attr.pop('blockingdhcp')

    def _normalize_dhcp(self, config_copy):
        for net_attr in config_copy.networks.itervalues():
            dhcp = net_attr.get('bootproto')
            if dhcp is None:
                net_attr['bootproto'] = 'none'
            else:
                net_attr['bootproto'] = dhcp
            net_attr['dhcpv6'] = net_attr.get('dhcpv6', False)
        return config_copy

    def _normalize_bonding_opts(self, config_copy):
        for bond, bond_attr in config_copy.bonds.iteritems():
            # TODO: globalize default bond options from Bond in models.py
            normalized_opts = self._parse_bond_options(
                bond_attr.get('options'))
            if "mode" not in normalized_opts:
                normalized_opts["mode"] = '0'
            bond_attr['options'] = self._netinfo.bondOptsForIfcfg(
                normalized_opts)
        # before d18e2f10 bondingOptions were also part of networks, so in case
        # we are upgrading from an older version, they should be ignored if
        # they exist.
        # REQUIRED_FOR upgrade from vdsm<=4.16.20
        for net_attr in config_copy.networks.itervalues():
            net_attr.pop('bondingOptions', None)

    def _normalize_bonding_nics(self, config_copy):
        for bond_attr in config_copy.bonds.itervalues():
            if 'nics' in bond_attr:
                bond_attr['nics'].sort()

    def _normalize_address(self, config_copy):
        for net_name, net_attr in six.iteritems(config_copy.networks):
            prefix = net_attr.pop('prefix', None)
            if prefix is not None:
                net_attr['netmask'] = self._netinfo.prefix2netmask(int(prefix))
            # Ignore IPv6 to avoid needless reconfiguration of networks
            if 'ipv6addr' in net_attr:
                del net_attr['ipv6addr']
            if 'ipv6gateway' in net_attr:
                del net_attr['ipv6gateway']

            if 'defaultRoute' not in net_attr:
                net_attr['defaultRoute'] = net_name in \
                    constants.LEGACY_MANAGEMENT_NETWORKS

    def _normalize_ifcfg_keys(self, config_copy):
        # ignore keys in persisted networks that might originate from vdsm-reg.
        # these might be a result of calling setupNetworks with ifcfg values
        # that come from the original interface that is serving the management
        # network. for 3.5, VDSM still supports passing arbitrary values
        # directly to the ifcfg files, e.g. 'IPV6_AUTOCONF=no'. we filter them
        # out here since kernelConfig will never report them.
        # TODO: remove when 3.5 is unsupported.
        def unsupported(key):
            return set(key) <= set(
                string.ascii_uppercase + string.digits + '_')

        for net_attr in config_copy.networks.itervalues():
            for k in net_attr.keys():
                if unsupported(k):
                    net_attr.pop(k)

    def _parse_bond_options(self, opts):
        if not opts:
            return {}

        opts = dict((pair.split('=', 1) for pair in opts.split()))

        # force a numeric bonding mode
        mode = opts.get('mode', self._netinfo.getDefaultBondingMode())
        if mode in _BONDING_MODES:
            numeric_mode = mode
        else:
            numeric_mode = _BONDING_MODES_REVERSED[mode]
            opts['mode'] = numeric_mode

        defaults = self._netinfo.getDefaultBondingOptions(numeric_mode)
        return dict(
            (k, v) for k, v in opts.iteritems() if v != defaults.get(k))


class RunningConfig(Config):
    def __init__(self):
        super(RunningConfig, self).__init__(CONF_RUN_DIR)

    def store(self):
        utils.execCmd([constants.EXT_VDSM_STORE_NET_CONFIG,
                       config.get('vars', 'net_persistence')])
        return PersistentConfig()


class PersistentConfig(Config):
    def __init__(self):
        super(PersistentConfig, self).__init__(CONF_PERSIST_DIR)

    def restore(self):
        restore()
        return RunningConfig()


def configuredPorts(nets, bridge):
    """Return the configured ports for the bridge"""
    if bridge not in nets:
        return []

    network = nets[bridge]
    nic = network.get('nic')
    bond = network.get('bonding')
    vlan = network.get('vlan', '')
    if bond:
        return [bond + vlan]
    elif nic:
        return [nic + vlan]
    else:  # isolated bridged network
        return []
