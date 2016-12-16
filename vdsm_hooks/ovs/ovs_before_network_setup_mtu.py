#!/usr/bin/python2
# Copyright 2015 Red Hat, Inc.
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
import six

from vdsm.network import ipwrapper
from vdsm.network import netinfo

from ovs_utils import iter_ovs_nets, iter_ovs_bonds


def _set_iface_mtu(iface, mtu):
    ipwrapper.linkSet(iface, ['mtu', str(mtu)])


def _update_mtu_changes(mtu, devices, changes):
    for device in devices:
        current_mtu = netinfo.mtus.getMtu(device)
        mtu = max(mtu, changes.get(device), current_mtu)
        if mtu != current_mtu:
            changes[device] = mtu


def _mtus_nics(running_config):
    """ Get MTUs for nics. Bondings MTUs will be changed as a consequence."""
    changes = {}
    for net, attrs in iter_ovs_nets(running_config.networks):
        mtu = attrs.get('mtu')
        if mtu is not None:
            nic = attrs.get('nic')
            bonding = attrs.get('bonding')
            if nic is not None:
                _update_mtu_changes(mtu, [nic], changes)
            elif bonding is not None:
                slaves = running_config.bonds[bonding].get('nics')
                _update_mtu_changes(mtu, slaves, changes)
    return changes


def _mtus_bonds(running_config):
    """ Bonding and its slaves should have the same MTU. Check slaves of
    every bonding, if not consistent, set them all the biggest found MTU.
    """
    changes = {}
    for bonding, attrs in iter_ovs_bonds(running_config.bonds):
        slaves = running_config.bonds[bonding].get('nics')
        mtu = max(netinfo.mtus.getMtu(bonding),
                  max([netinfo.mtus.getMtu(slave) for slave in slaves]))
        _update_mtu_changes(mtu, slaves, changes)
    return changes


def _mtus_vlans(running_config):
    """ OVS vlans MTUs are automaticaly changed to the lowest MTU of
    underlying devices. However, in VDSM, vlan's MTU is based on network
    settings. In case when current vlan's MTU differs (should be lower than
    a minimal underlying device's MTU), get needed changes.
    """
    changes = {}
    for net, attrs in iter_ovs_nets(running_config.networks):
        mtu = attrs.get('mtu')
        if mtu is not None and 'vlan' in attrs:
            current_mtu = netinfo.mtus.getMtu(net)
            if current_mtu != mtu:
                changes[net] = mtu
    return changes


def configure_mtu(running_config):
    """ Setup MTUs for OVS networks. We have to do this in three iterations
    because of change of a lesser device could influence an upper. This way we
    keep MTU handling compatible with standard VDSM.
    """
    changes_nics = _mtus_nics(running_config)
    changes_bonds = _mtus_bonds(running_config)
    changes_vlans = _mtus_vlans(running_config)
    for mtu_changes in (changes_nics, changes_bonds, changes_vlans):
        for iface, mtu in six.iteritems(mtu_changes):
            _set_iface_mtu(iface, mtu)
