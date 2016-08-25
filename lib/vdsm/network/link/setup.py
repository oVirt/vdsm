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

import six

from vdsm.network.ip import address
from vdsm.network.ip import dhclient

from .bond import Bond


class SetupBonds(object):
    def __init__(self, bonds2add, bonds2edit, bonds2remove, config):
        self._bonds2add = bonds2add
        self._bonds2edit = bonds2edit
        self._bonds2remove = bonds2remove
        self._config = config
        self._acquired_ifaces = set()

    def remove_bonds(self):
        for bond_name in self._bonds2remove:
            with Bond(bond_name) as bond:
                bond.destroy()
            self._config.removeBonding(bond_name)

    def edit_bonds(self):
        """
        Editing bonds requires a special treatment due to the required steps
        to perform editation on a bulk of bonds.
        To support scenarios of moving slaves between bonds,
        the following algorithm has been chosen:
        Go over all bonds twice, once to remove slaves and
        secondly to add slaves.

        When we split the bond editing like that, we break a single bond
        transaction into two parts: removing and adding slaves.
        In case the slave-add transaction fails, it is up to the upper level
        to revert the change.
        """
        # TODO: Create a SetupBonds transaction.
        init_bond_pool = [(Bond(bond_name), attrs)
                          for bond_name, attrs in self._bonds2edit.items()]

        for bond, attrs in init_bond_pool:
            with bond:
                slaves2remove = self._slaves2remove(bond.slaves,
                                                    frozenset(attrs['nics']))
                bond.del_slaves(slaves2remove)

            # Saving only a partial bond config, overwritten in the next step.
            self._config.setBonding(bond.master, {'nics': list(bond.slaves)})

        for bond, attrs in init_bond_pool:
            with bond:
                requested_slaves = frozenset(attrs['nics'])
                slaves2add = self._slaves2add(bond.slaves, requested_slaves)
                bond.add_slaves(slaves2add)

            # TODO: Options
            # attrs.get('options', '')

            self._config.setBonding(bond.master, attrs)

            bond.up()
            _ip_flush(slaves2add)

    def add_bonds(self):
        for bond_name, attrs in six.iteritems(self._bonds2add):
            requested_slaves = set(attrs['nics'])
            with Bond(bond_name, slaves=requested_slaves) as bond:
                bond.create()

            # TODO: Options
            # attrs.get('options', '')

            self._config.setBonding(bond_name, attrs)

            bond.up()
            _ip_flush(requested_slaves)

    @property
    def ifaces_for_acquirement(self):
        acquire_ifaces = set()
        for bond_name, attrs in six.iteritems(self._bonds2add):
            acquire_ifaces |= set(attrs['nics']) | {bond_name}
        for bond_name, attrs in six.iteritems(self._bonds2edit):
            acquire_ifaces |= set(attrs['nics']) | {bond_name}

        return acquire_ifaces

    def _slaves2remove(self, running_slaves, requested_slaves):
        return running_slaves - requested_slaves

    def _slaves2add(self, running_slaves, requested_slaves):
        return requested_slaves - running_slaves


def _ip_flush(ifaces):
    # TODO: Tell NetworkManager to unmanage this iface.
    for iface in ifaces:
        dhclient.kill(iface, family=4)
        dhclient.kill(iface, family=6)
        address.flush(iface)
