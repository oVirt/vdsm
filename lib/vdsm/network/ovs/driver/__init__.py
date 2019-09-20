# Copyright 2016-2017 Red Hat, Inc.
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
from __future__ import division
from __future__ import print_function

import abc

import six

from vdsm.network import driverloader


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
    def execute(self, timeout):
        pass


@six.add_metaclass(abc.ABCMeta)
class OvsApi(object):
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

    @abc.abstractmethod
    def set_dpdk_port(self, port, pci_addr):
        pass

    @abc.abstractmethod
    def set_vhostuser_iface(self, iface, socket_path):
        pass

    @abc.abstractmethod
    def del_port(self, port, bridge=None, if_exists=False):
        pass

    @abc.abstractmethod
    def list_ports(self, bridge):
        pass

    @abc.abstractmethod
    def add_mirror(self, bridge, mirror, output_port):
        pass

    @abc.abstractmethod
    def del_mirror(self, bridge, mirror):
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

    def list_mirror_info(self, mirror=None):
        return self.list_db_table('Mirror', mirror)

    def set_mirror_attr(self, mirror, key, value):
        return self.set_db_entry('Mirror', mirror, key, value)


class Drivers(object):
    VSCTL = 'vsctl'


def create(driver_name=Drivers.VSCTL):
    _drivers = driverloader.load_drivers('Ovs', __name__, __path__[0])
    ovs_driver = driverloader.get_driver(driver_name, _drivers)
    return ovs_driver()
