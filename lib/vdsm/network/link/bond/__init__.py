# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

import abc

from vdsm.network import driverloader
from vdsm.network.link.iface import iface
from vdsm.network.netlink import waitfor

from .bond_speed import speed

speed


class BondAPI(object):
    """
    Bond driver interface.
    """

    __metaclass__ = abc.ABCMeta

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
