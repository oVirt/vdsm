# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division
from copy import deepcopy
import errno
import json
import logging
import os
import shutil

import six

from vdsm.common import constants
from vdsm.common import fileutils

from vdsm.network.link.iface import random_iface_name
from . import errors as ne

NETCONF_BONDS = 'bonds'
NETCONF_NETS = 'nets'
NETCONF_DEVS = 'devices'

CONF_RUN_DIR = constants.P_VDSM_LIB + 'staging/netconf'
CONF_PERSIST_DIR = constants.P_VDSM_LIB + 'persistence/netconf'

VOLATILE_NET_ATTRS = ('blockingdhcp',)


class BaseConfig(object):
    def __init__(self, networks, bonds, devices):
        self.networks = networks
        self.bonds = bonds
        self.devices = devices

    def setNetwork(self, network, attrs):
        cleanAttrs = BaseConfig._filter_out_net_attrs(attrs)
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

    def set_device(self, devname, devattrs):
        self.devices[devname] = devattrs
        logging.info('Setting device %s(%s)', devname, devattrs)

    def remove_device(self, devname):
        try:
            del self.devices[devname]
            logging.info('Removing device %s', devname)
        except KeyError:
            logging.debug('Device %s not found for removal', devname)

    def diffFrom(self, other):
        """Returns a diff Config that shows what should be changed for
        going from other to self."""
        # TODO: The new devices config is not handled
        diff = BaseConfig(
            self._confDictDiff(self.networks, other.networks),
            self._confDictDiff(self.bonds, other.bonds),
            {},
        )
        return diff

    def __eq__(self, other):
        # TODO: The new devices config is not handled
        return self.networks == other.networks and self.bonds == other.bonds

    def __hash__(self):
        return hash((self.networks, self.bonds))

    def __repr__(self):
        return '%s(%s, %s, %s)' % (
            self.__class__.__name__,
            self.networks,
            self.bonds,
            self.devices,
        )

    def __bool__(self):
        # TODO: The new devices config is not handled
        return True if self.networks or self.bonds else False

    # TODO: drop when py2 is no longer needed
    __nonzero__ = __bool__

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
        # TODO: The new devices config is not handled
        return {
            'networks': json.loads(json.dumps(self.networks)),
            'bonds': json.loads(json.dumps(self.bonds)),
        }

    @staticmethod
    def _filter_out_net_attrs(netattrs):
        attrs = {
            key: value
            for key, value in six.viewitems(netattrs)
            if value is not None
        }

        _filter_out_volatile_net_attrs(attrs)
        return attrs


class Config(BaseConfig):
    def __init__(self, savePath):
        self.netconf_path = savePath
        self.networksPath = os.path.join(savePath, NETCONF_NETS)
        self.bondingsPath = os.path.join(savePath, NETCONF_BONDS)
        self.devicesPath = os.path.join(savePath, NETCONF_DEVS)
        nets = self._getConfigs(self.networksPath)
        for net_attrs in six.viewvalues(nets):
            _filter_out_volatile_net_attrs(net_attrs)
        bonds = self._getConfigs(self.bondingsPath)
        devices = self._getConfigs(self.devicesPath)
        super(Config, self).__init__(nets, bonds, devices)

    def delete(self):
        self.networks = {}
        self.bonds = {}
        self.devices = {}
        self._clearDisk()

    def save(self):
        self._clearDisk()
        rand_suffix = random_iface_name(max_length=8)
        rand_netconf_path = self.netconf_path + '.' + rand_suffix
        rand_nets_path = os.path.join(rand_netconf_path, NETCONF_NETS)
        rand_bonds_path = os.path.join(rand_netconf_path, NETCONF_BONDS)
        rand_devs_path = os.path.join(rand_netconf_path, NETCONF_DEVS)

        self._save_config(self.networks, rand_nets_path)
        self._save_config(self.bonds, rand_bonds_path)
        self._save_config(self.devices, rand_devs_path)

        _atomic_copytree(rand_netconf_path, self.netconf_path, remove_src=True)

        logging.info(
            'Saved new config %r to [%s,%s,%s]',
            self,
            self.networksPath,
            self.bondingsPath,
            self.devicesPath,
        )

    def _save_config(self, configs, configpath):
        os.makedirs(configpath)
        for configname, attrs in six.iteritems(configs):
            self._setConfig(attrs, os.path.join(configpath, configname))

    @staticmethod
    def _getConfigDict(path):
        try:
            with open(path, 'r') as configurationFile:
                return json.load(configurationFile)
        except IOError as ioe:
            if ioe.errno == errno.ENOENT:
                logging.debug('Network entity at %s not found', path)
                return {}
            else:
                raise

    @staticmethod
    def _getConfigs(path):
        if not os.path.exists(path):
            return {}

        networkEntities = {}

        for fileName in os.listdir(path):
            fullPath = os.path.join(path, fileName)
            networkEntities[fileName] = Config._getConfigDict(fullPath)

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

    def _clearDisk(self):
        logging.info('Clearing netconf: %s', self.netconf_path)
        self._clear_config(self.netconf_path)

    @staticmethod
    def _clear_config(confpath):
        try:
            real_confpath = os.path.realpath(confpath)
            fileutils.rm_file(confpath)
            fileutils.rm_tree(real_confpath)
        except OSError as e:
            # Support the "old" non-symlink config path.
            if e.errno == errno.EISDIR:
                fileutils.rm_tree(confpath)
            else:
                raise


class RunningConfig(Config):
    def __init__(self):
        super(RunningConfig, self).__init__(CONF_RUN_DIR)

    @staticmethod
    def store():
        """
        Declare the current running config as 'safe' and persist this safe
        config.

        It is implemented by copying the running config to the
        persistent (safe) config in an atomic manner.
        """
        _atomic_copytree(CONF_RUN_DIR, CONF_PERSIST_DIR)


class PersistentConfig(Config):
    def __init__(self):
        super(PersistentConfig, self).__init__(CONF_PERSIST_DIR)


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
                exc_info=(ex_type, ex_value, ex_traceback),
            )
        else:
            config_diff = self.base_config.diffFrom(self.config)
            if config_diff:
                logging.warning(
                    'Failed setup transaction,'
                    'reverting to last known good network.',
                    exc_info=(ex_type, ex_value, ex_traceback),
                )
                raise ne.RollbackIncomplete(config_diff, ex_type, ex_value)


def _filter_out_volatile_net_attrs(net_attrs):
    for attr in VOLATILE_NET_ATTRS:
        net_attrs.pop(attr, None)


def _atomic_copytree(srcpath, dstpath, remove_src=False):
    """
    Copy srcpath to dstpatch in an atomic manner.

    It applies atomic directory copy by using the atomicity of overwriting a
    link (rename syscall).

    In case the remove_src flag argument is True, the srcpath is deleted.

    Note: In case of an interruption, it is assured that the destination is
    intact, pointing to the previous data or the new one. Intermediate
    temporary files  or the srcpath may still exists on the filesystem.
    """
    rand_suffix = random_iface_name(max_length=8)
    rand_dstpath = dstpath + '.' + rand_suffix
    rand_dstpath_symlink = rand_dstpath + '.ln'

    shutil.copytree(srcpath, rand_dstpath)
    os.symlink(rand_dstpath, rand_dstpath_symlink)

    old_realdstpath = os.path.realpath(dstpath)
    old_realdstpath_existed = old_realdstpath != dstpath

    _fsynctree(rand_dstpath)

    os.rename(rand_dstpath_symlink, dstpath)
    if old_realdstpath_existed:
        fileutils.rm_tree(old_realdstpath)
    if remove_src:
        fileutils.rm_tree(srcpath)


def _fsynctree(path):
    filepaths = (
        os.path.join(rootdir, filename)
        for rootdir, _, file_names in os.walk(path)
        for filename in file_names
    )

    for f in filepaths:
        _fsyncpath(f)


def _fsyncpath(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
