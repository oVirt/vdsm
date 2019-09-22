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

import abc

import six

from vdsm.network import driverloader
from vdsm.network.link.iface import iface
from vdsm.network.netlink import waitfor

from .speed import speed

speed


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
        with waitfor.waitfor_linkup(self._master, timeout=2):
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
        master = iface(self._master)
        if up:
            master.up()
        else:
            master.down()
        for s in self._slaves:
            slave = iface(s)
            if up:
                slave.up()
            else:
                slave.down()


class Drivers(object):
    SYSFS = 'sysfs_driver'


def driver(driver_name=Drivers.SYSFS):
    _drivers = driverloader.load_drivers('Bond', __name__, __path__[0])
    return driverloader.get_driver(driver_name, _drivers)


Bond = driver()
