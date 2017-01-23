# Copyright 2016 Red Hat, Inc.
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

import abc
from importlib import import_module
from pkgutil import iter_modules

import six

from vdsm.network.link import iface


@six.add_metaclass(abc.ABCMeta)
class BondAPI(object):
    """
    Bond driver interface.
    """
    def __init__(self, name, slaves=(), options=None):
        self._master = name
        self._slaves = set(slaves)
        self._options = options
        self._properties = {}
        if self.exists():
            self._import_existing()

    def __enter__(self):
        return self

    def __exit__(self, type, value, traceback):
        pass

    @abc.abstractmethod
    def create(self):
        pass

    @abc.abstractmethod
    def destroy(self):
        pass

    @abc.abstractmethod
    def add_slaves(self, slaves):
        pass

    @abc.abstractmethod
    def del_slaves(self, slaves):
        pass

    @abc.abstractmethod
    def set_options(self, options):
        """
        Set bond options, overriding existing or default ones.
        """
        pass

    @abc.abstractmethod
    def exists(self):
        pass

    @abc.abstractmethod
    def active_slave(self):
        pass

    @staticmethod
    def bonds():
        pass

    @property
    def master(self):
        return self._master

    @property
    def slaves(self):
        return self._slaves

    @property
    def options(self):
        return self._options

    @property
    def properties(self):
        return self._properties

    def up(self):
        self._setlinks(up=True)

    def down(self):
        self._setlinks(up=False)

    def refresh(self):
        if self.exists():
            self._import_existing()

    @abc.abstractmethod
    def _import_existing(self):
        pass

    def _setlinks(self, up):
        setstate = iface.up if up else iface.down
        setstate(self._master)
        for slave in self._slaves:
            setstate(slave)


DEFAULT_DRIVER = 'sysfs_driver'
_DRIVERS = {}


# Importing all available bond drivers.
for _, module_name, _ in iter_modules([__path__[0]]):
    module = import_module('{}.{}'.format(__name__, module_name))
    if hasattr(module, 'Bond'):
        _DRIVERS[module_name] = module


def _bond_driver(driver=DEFAULT_DRIVER):
    """Bond driver factory."""
    return _DRIVERS[driver].Bond


Bond = _bond_driver()
