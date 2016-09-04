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
import os
import six

from . import iface


@six.add_metaclass(abc.ABCMeta)
class BondAPI(object):
    """
    Bond driver interface.
    """
    def __init__(self, name, slaves=(), options=None):
        self._master = name
        self._slaves = set(slaves)
        self._options = options
        if self.exists():
            self._import_existing()

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

    def up(self):
        self._setlinks(up=True)

    def down(self):
        self._setlinks(up=False)

    @abc.abstractmethod
    def _import_existing(self):
        pass

    def _setlinks(self, up):
        setstate = iface.up if up else iface.down
        setstate(self._master)
        for slave in self._slaves:
            setstate(slave)


class BondSysFS(BondAPI):

    BONDING_MASTERS = '/sys/class/net/bonding_masters'
    BONDING_PATH = '/sys/class/net/%s/bonding'
    BONDING_SLAVES = BONDING_PATH + '/slaves'
    BONDING_ACTIVE_SLAVE = BONDING_PATH + '/active_slave'
    BONDING_OPT = BONDING_PATH + '/%s'

    def __init__(self, name, slaves=(), options=None):
        super(BondSysFS, self).__init__(name, slaves, options)

    def create(self):
        with open(self.BONDING_MASTERS, 'w') as f:
            f.write('+%s' % self._master)
        if self._slaves:
            self.add_slaves(self._slaves)

    def destroy(self):
        with open(self.BONDING_MASTERS, 'w') as f:
            f.write('-%s' % self._master)

    def add_slaves(self, slaves):
        for slave in slaves:
            iface.down(slave)
            with open(self.BONDING_SLAVES % self._master, 'w') as f:
                f.write('+%s' % slave)
            self._slaves.add(slave)

    def del_slaves(self, slaves):
        for slave in slaves:
            iface.down(slave)
            with open(self.BONDING_SLAVES % self._master, 'w') as f:
                f.write('-%s' % slave)
            self._slaves.remove(slave)

    def set_options(self, options):
        self._options = dict(options)
        for key, value in options:
            with open(self.BONDING_OPT % (self._master, key), 'w') as f:
                f.write(value)

    def exists(self):
        return os.path.exists(self.BONDING_PATH % self._master)

    def active_slave(self):
        with open(self.BONDING_ACTIVE_SLAVE % self._master) as f:
            return f.readline().rstrip()

    @staticmethod
    def bonds():
        with open(BondSysFS.BONDING_MASTERS) as f:
            return f.read().rstrip().split()

    def _import_existing(self):
        with open(self.BONDING_SLAVES % self._master) as f:
            self._slaves = set(f.readline().split())
        # TODO: Support options
        self._options = None


# TODO: Use a configuration parameter to determine which driver to use.
def _bond_driver():
    """
    Return the bond driver implementation.
    """
    return BondSysFS


Bond = _bond_driver()
