#
# Copyright 2013-2017 Red Hat, Inc.
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
from copy import deepcopy
import errno
import json
import logging
import os

import six

from vdsm.config import config
from vdsm.tool.restore_nets import restore
from vdsm import commands
from vdsm import constants
from vdsm import utils
from . import errors as ne
from .canonicalize import canonicalize_networks, canonicalize_bondings

CONF_RUN_DIR = constants.P_VDSM_LIB + 'staging/netconf/'
# The persistent path is inside of an extra "persistence" dir in order to get
# oVirt Node to persist the symbolic links that are necessary for the
# atomic storage of running config into persistent config.
CONF_PERSIST_DIR = constants.P_VDSM_LIB + 'persistence/netconf/'


class BaseConfig(object):
    def __init__(self, networks, bonds):
        self.networks = networks
        self.bonds = bonds

    def setNetwork(self, network, attrs):
        # Clean netAttrs from fields that should not be serialized
        cleanAttrs = dict((key, value) for key, value in six.iteritems(attrs)
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

    def __bool__(self):
        return True if self.networks or self.bonds else False

    def __nonzero__(self):  # TODO: drop when py2 is no longer needed
        return self.__bool__()

    @staticmethod
    def _confDictDiff(lhs, rhs):
        result = {}
        for name in rhs:
            if name not in lhs:
                result[name] = {'remove': True}

        for name, attr in six.iteritems(lhs):
            if name not in rhs or attr != rhs[name]:
                result[name] = lhs[name]
        return result

    def as_unicode(self):
        return {'networks': json.loads(json.dumps(self.networks)),
                'bonds': json.loads(json.dumps(self.bonds))}


class Config(BaseConfig):
    def __init__(self, savePath):
        self.networksPath = os.path.join(savePath, 'nets', '')
        self.bondingsPath = os.path.join(savePath, 'bonds', '')
        nets = self._getConfigs(self.networksPath)
        canonicalize_networks(nets)
        bonds = self._getConfigs(self.bondingsPath)
        canonicalize_bondings(bonds)
        super(Config, self).__init__(nets, bonds)

    def delete(self):
        self.networks = {}
        self.bonds = {}
        self._clearDisk()

    def save(self):
        self._clearDisk()
        for bond, attrs in six.iteritems(self.bonds):
            self._setConfig(attrs, self._bondingPath(bond))
        for network, attrs in six.iteritems(self.networks):
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

    @staticmethod
    def _removeConfig(path):
        utils.rmFile(path)

    def _clearDisk(self):
        def _clear_dir(path_to_dir):
            try:
                for filePath in os.listdir(path_to_dir):
                    self._removeConfig(path_to_dir + filePath)
            except OSError as ose:
                if ose.errno == errno.ENOENT:
                    logging.debug('No existent config to clear on %s' %
                                  path_to_dir)
                else:
                    raise

        logging.info('Clearing %s and %s', self.networksPath,
                     self.bondingsPath)
        _clear_dir(self.networksPath)
        _clear_dir(self.bondingsPath)


class RunningConfig(Config):
    def __init__(self):
        super(RunningConfig, self).__init__(CONF_RUN_DIR)

    def store(self):
        commands.execCmd([constants.EXT_VDSM_STORE_NET_CONFIG,
                         config.get('vars', 'net_persistence')])
        return PersistentConfig()


class PersistentConfig(Config):
    def __init__(self):
        super(PersistentConfig, self).__init__(CONF_PERSIST_DIR)

    def restore(self):
        restore()
        return RunningConfig()


class Transaction(object):
    def __init__(self, config=None, persistent=True, in_rollback=False):
        self.config = config if config is not None else RunningConfig()
        self.base_config = deepcopy(self.config)
        self.persistent = persistent
        self.in_rollback = in_rollback

    def __enter__(self):
        return self.config

    def __exit__(self, ex_type, ex_value, ex_traceback):
        if ex_type is None:
            if self.persistent:
                self.config.save()
        elif self.in_rollback:
            logging.error(
                'Failed rollback transaction to last known good network.',
                exc_info=(ex_type, ex_value, ex_traceback))
        else:
            config_diff = self.base_config.diffFrom(self.config)
            if config_diff:
                logging.warning(
                    'Failed setup transaction,'
                    'reverting to last known good network.',
                    exc_info=(ex_type, ex_value, ex_traceback))
                raise ne.RollbackIncomplete(config_diff, ex_type, ex_value)


def configuredPorts(nets, bridge):
    """Return the configured ports for the bridge"""
    if bridge not in nets:
        return []

    network = nets[bridge]
    nic = network.get('nic')
    bond = network.get('bonding')
    vlan = str(network.get('vlan', ''))
    if bond:
        return [bond + vlan]
    elif nic:
        return [nic + vlan]
    else:  # isolated bridged network
        return []
