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

from contextlib import contextmanager

import six

from vdsm.network.netconfpersistence import RunningConfig
from vdsm.network.netinfo.nics import nics

from . import info
from . import validator

SWITCH_TYPE = 'ovs'


def validate_network_setup(nets, bonds):
    running_bonds = info.get_netinfo()['bondings']
    kernel_nics = nics()
    for net, attrs in six.iteritems(nets):
        validator.validate_net_configuration(
            net, attrs, bonds, running_bonds, kernel_nics)
    for bond, attrs in six.iteritems(bonds):
        validator.validate_bond_configuration(attrs, kernel_nics)


@contextmanager
def rollback_trigger(in_rollback):
    try:
        yield
    except:
        pass
    finally:
        pass


def setup(nets, bonds):
    ovs_info = info.OvsInfo()
    _netinfo = info.create_netinfo(ovs_info)
    nets_to_be_added, nets_to_be_removed = _split_nets_action(
        nets, _netinfo['networks'])
    bonds_to_be_added_or_edited, bonds_to_be_removed = _split_bonds_action(
        bonds, _netinfo['bondings'])

    # TODO: remove and add filtered networks


def _split_nets_action(nets, running_nets):
    # TODO: If a nework is to be edited, we remove it and recreate again.
    # We should implement editation.
    nets_to_be_removed = set()
    nets_to_be_added = {}

    for net, attrs in six.iteritems(nets):
        if 'remove' in attrs:
            nets_to_be_removed.add(net)
        elif net not in running_nets:
            nets_to_be_added[net] = attrs
        elif attrs != running_nets.get(net):
            nets_to_be_removed.add(net)
            nets_to_be_added[net] = attrs

    return nets_to_be_added, nets_to_be_removed


def _split_bonds_action(bonds, configured_bonds):
    bonds_to_be_removed = set()
    bonds_to_be_added_or_edited = {}

    for bond, attrs in six.iteritems(bonds):
        if 'remove' in attrs:
            bonds_to_be_removed.add(bond)
        elif attrs != configured_bonds.get(bond):
            bonds_to_be_added_or_edited[bond] = attrs

    return bonds_to_be_added_or_edited, bonds_to_be_removed
