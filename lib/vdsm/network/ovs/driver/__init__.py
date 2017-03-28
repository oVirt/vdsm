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
from __future__ import print_function

import abc
from importlib import import_module
from pkgutil import iter_modules

import six


DEFAULT_DRIVER = 'vsctl'
_DRIVERS = {}


def create(driver=DEFAULT_DRIVER):
    """OVS driver factory."""
    return _DRIVERS[driver].create()


@six.add_metaclass(abc.ABCMeta)
class Transaction(object):

    @abc.abstractmethod
    def commit(self):
        """Apply all Commands in the Transaction."""

    @abc.abstractmethod
    def add(self, *commands):
        """Append Commands to the Transaction."""

    def __enter__(self):
        return self

    def __exit__(self, ex_type, ex_val, tb):
        if ex_type is None:
            self.result = self.commit()


@six.add_metaclass(abc.ABCMeta)
class Command(object):
    """An OVS Command which is to be executed in Transaction."""
    @abc.abstractmethod
    def execute(self):
        pass


@six.add_metaclass(abc.ABCMeta)
class API(object):
    """
    Abstact class for driver implementations.
    Each method returns a Command instance.
    """
    @abc.abstractmethod
    def transaction(self):
        pass

    @abc.abstractmethod
    def add_br(self, bridge, may_exist=False):
        pass

    def set_dpdk_bridge(self, bridge):
        return self.set_db_entry('bridge', bridge, 'datapath_type', 'netdev')

    @abc.abstractmethod
    def del_br(self, bridge, if_exists=False):
        pass

    @abc.abstractmethod
    def list_br(self):
        pass

    @abc.abstractmethod
    def add_vlan(self, bridge, vlan, fake_bridge_name=None, may_exist=False):
        pass

    @abc.abstractmethod
    def del_vlan(self, vlan, fake_bridge_name=None, if_exist=False):
        pass

    @abc.abstractmethod
    def add_bond(self, bridge, bond, nics, fake_iface=False, may_exist=False):
        pass

    @abc.abstractmethod
    def attach_bond_slave(self, bond, slave):
        pass

    @abc.abstractmethod
    def detach_bond_slave(self, bond, slave):
        pass

    @abc.abstractmethod
    def add_port(self, bridge, port, may_exist=False):
        pass

    def set_dpdk_port(self, port):
        return self.set_interface_attr(port, 'type', 'dpdk')

    @abc.abstractmethod
    def del_port(self, port, bridge=None, if_exists=False):
        pass

    @abc.abstractmethod
    def list_ports(self, bridge):
        pass

    @abc.abstractmethod
    def do_nothing(self):
        """None equivalent for Command."""

    @abc.abstractmethod
    def list_db_table(self, table, row=None):
        pass

    @abc.abstractmethod
    def set_db_entry(self, table, row, key, value):
        pass

    def list_bridge_info(self, bridge=None):
        return self.list_db_table('Bridge', bridge)

    def set_bridge_attr(self, bridge, key, value):
        return self.set_db_entry('Bridge', bridge, key, value)

    def list_port_info(self, port=None):
        return self.list_db_table('Port', port)

    def set_port_attr(self, port, key, value):
        return self.set_db_entry('Port', port, key, value)

    def list_interface_info(self, iface=None):
        return self.list_db_table('Interface', iface)

    def set_interface_attr(self, iface, key, value):
        return self.set_db_entry('Interface', iface, key, value)


# Importing all available drivers from the ovs.driver package.
for _, module, _ in iter_modules([__path__[0]]):
    _DRIVERS[module] = import_module('{}.{}'.format(__name__, module))
