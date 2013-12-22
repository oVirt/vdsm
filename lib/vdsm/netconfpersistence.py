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

import errno
import json
import logging
import os

from .config import config
from .tool.restore_nets import restore
from . import constants
from . import utils


CONF_RUN_DIR = constants.P_VDSM_RUN + 'netconf/'
# The persistent path is inside of an extra "persistence" dir in order to get
# oVirt Node to persist the symbolic links that are necessary for the
# atomic storage of running config into persistent config.
CONF_PERSIST_DIR = constants.P_VDSM_LIB + 'persistence/netconf/'


class BaseConfig(object):
    def __init__(self, networks, bonds):
        self.networks = networks
        self.bonds = bonds

    def __eq__(self, other):
        return self.networks == other.networks and self.bonds == other.bonds

    def __repr__(self):
        return '%s(%s, %s)' % (self.__class__.__name__, self.networks,
                               self.bonds)

    def __nonzero__(self):
        return True if self.networks or self.bonds else False

    def diffFrom(self, other):
        """Returns a diff Config that shows the what should be changed for
        going from other to self."""
        diff = BaseConfig(self._confDictDiff(self.networks, other.networks),
                          self._confDictDiff(self.bonds, other.bonds))
        return diff

    def setNetwork(self, network, attributes):
        # Clean netAttrs from fields that should not be serialized
        cleanAttrs = dict((key, value) for key, value in attributes.iteritems()
                          if value is not None and key not in
                          ('configurator', '_netinfo', 'force',
                           'implicitBonding'))
        self.networks[network] = cleanAttrs
        logging.info('Adding network %s(%s)' % (network, cleanAttrs))

    def removeNetwork(self, network):
        try:
            del self.networks[network]
            logging.info('Removing network %s' % network)
        except KeyError:
            logging.debug('Network %s not found for removal' % network)

    def setBonding(self, bonding, attributes):
        self.bonds[bonding] = attributes
        logging.info('Adding %s(%s)' % (bonding, attributes))

    def removeBonding(self, bonding):
        try:
            del self.bonds[bonding]
            logging.info('Removing %s' % bonding)
        except KeyError:
            logging.debug('%s not found for removal' % bonding)

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
                logging.debug('Network entity at %s not found' % path)
                return {}
            else:
                raise

    def _getConfigs(self, path):
        networkEntities = {}
        try:
            for fileName in os.listdir(path):
                fullPath = path + fileName
                networkEntities[fileName] = self._getConfigDict(fullPath)
        except OSError as ose:
            if ose.errno == errno.ENOENT:
                logging.debug('Non-existing config set.')
            else:
                raise

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
            json.dump(config, configurationFile)

    @staticmethod
    def _removeConfig(path):
        utils.rmFile(path)

    def _clearDisk(self):
        try:
            logging.info('Clearing %s and %s' % (self.networksPath,
                                                 self.bondingsPath))
            for filePath in os.listdir(self.networksPath):
                self._removeConfig(self.networksPath + filePath)

            for filePath in os.listdir(self.bondingsPath):
                self._removeConfig(self.bondingsPath + filePath)
        except OSError as ose:
            if ose.errno == errno.ENOENT:
                logging.debug('No existent config to clear.')
            else:
                raise

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
