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
from vdsm.network.libvirt import networks as libvirt_nets
from vdsm.network.netinfo.cache import (libvirtNets2vdsm, get as netinfo_get,
                                        CachingNetInfo)
from vdsm.tool.service import service_status
from vdsm.utils import memoized

from . import connectivity
from . import legacy_switch
from . import errors as ne
from .ovs import switch as ovs_switch, info as ovs_info
from .netconfpersistence import RunningConfig


def _split_switch_type_entries(entries, running_entries):
    legacy_entries = {}
    ovs_entries = {}

    def store_broken_entry(name, attrs):
        """
        If a network/bond should be removed but its existing entry was not
        found in running config, we have to find out what switch type has to
        be used for removal on our own.

        All we do now is, that we pass orphan entry to legacy swich which is
        (unlike OVS switch) able to remove broken networks/bonds.

        TODO: Try to find out which switch type should be used for broken
        network/bonding removal.
        """
        legacy_entries[name] = attrs

    def store_entry(name, attrs, switch_type):
        if switch_type is None:
            store_broken_entry(name, attrs)
        elif switch_type == legacy_switch.SWITCH_TYPE:
            legacy_entries[name] = attrs
        elif switch_type == ovs_switch.SWITCH_TYPE:
            ovs_entries[name] = attrs
        else:
            raise ne.ConfigNetworkError(
                ne.ERR_BAD_PARAMS, 'Invalid switch type %s' % attrs['switch'])

    for name, attrs in six.iteritems(entries):
        if 'remove' in attrs:
            running_attrs = running_entries.get(name, {})
            switch_type = running_attrs.get('switch')
        else:
            switch_type = attrs['switch']
        store_entry(name, attrs, switch_type)

    return legacy_entries, ovs_entries


def _split_switch_type(nets, bonds):
    _netinfo = netinfo()
    legacy_nets, ovs_nets = _split_switch_type_entries(
        nets, _netinfo['networks'])
    legacy_bonds, ovs_bonds = _split_switch_type_entries(
        bonds, _netinfo['bondings'])
    return legacy_nets, ovs_nets, legacy_bonds, ovs_bonds


def validate(networks, bondings):
    legacy_nets, ovs_nets, legacy_bonds, ovs_bonds = _split_switch_type(
        networks, bondings)

    use_legacy_switch = legacy_nets or legacy_bonds
    use_ovs_switch = ovs_nets or ovs_bonds
    if use_legacy_switch and use_ovs_switch:
        raise ne.ConfigNetworkError(
            ne.ERR_BAD_PARAMS,
            'Mixing of legacy and OVS networks is not supported inside one '
            'setupNetworks() call.')

    if use_legacy_switch:
        legacy_switch.validate_network_setup(legacy_nets, legacy_bonds)
    elif use_ovs_switch:
        ovs_switch.validate_network_setup(ovs_nets, ovs_bonds)


def setup(networks, bondings, options, in_rollback):
    legacy_nets, ovs_nets, legacy_bonds, ovs_bonds = _split_switch_type(
        networks, bondings)

    use_legacy_switch = legacy_nets or legacy_bonds
    use_ovs_switch = ovs_nets or ovs_bonds

    if use_legacy_switch:
        _setup_legacy(legacy_nets, legacy_bonds, options, in_rollback)
    elif use_ovs_switch:
        _setup_ovs(ovs_nets, ovs_bonds, options, in_rollback)


def _setup_legacy(networks, bondings, options, in_rollback):

    _libvirt_nets = libvirt_nets()
    _netinfo = CachingNetInfo(netinfo_get(libvirtNets2vdsm(_libvirt_nets)))

    with legacy_switch.ConfiguratorClass(in_rollback) as configurator:
        # from this point forward, any exception thrown will be handled by
        # Configurator.__exit__.

        legacy_switch.remove_networks(networks, bondings, configurator,
                                      _netinfo, _libvirt_nets)

        legacy_switch.bonds_setup(bondings, configurator, _netinfo,
                                  in_rollback)

        legacy_switch.add_missing_networks(configurator, networks,
                                           bondings, _netinfo)

        connectivity.check(options)


def _setup_ovs(networks, bondings, options, in_rollback):
    _ovs_info = ovs_info.OvsInfo()
    ovs_netinfo = ovs_info.create_netinfo(_ovs_info)

    nets2add, nets2edit, nets2remove = _split_setup_actions(
        networks, ovs_netinfo['networks'])
    bonds2add, bonds2edit, bonds2remove = _split_setup_actions(
        bondings, ovs_netinfo['bondings'])

    # TODO: If a nework is to be edited, we remove it and recreate again.
    # We should implement editation.
    nets2add.update(nets2edit)
    nets2remove.update(nets2edit)

    with ovs_switch.transaction(in_rollback, networks, bondings):
        ovs_switch.setup(_ovs_info, nets2add, nets2remove, bonds2add,
                         bonds2edit, bonds2remove)
        _setup_ipv6autoconf(networks)
        connectivity.check(options)


def ovs_net2bridge(network_name):
    return ovs_info.northbound2bridge(network_name)


def net2vlan(network_name):
    # Using RunningConfig avoids the need to require root access.
    net_attr = RunningConfig().networks.get(network_name)
    return net_attr.get('vlan') if net_attr else None


def netinfo(compatibility=None):
    # TODO: Version requests by engine to ease handling of compatibility.
    _netinfo = netinfo_get(compatibility=compatibility)

    if _is_ovs_service_running():
        ovs_netinfo = ovs_info.get_netinfo()

        running_networks = RunningConfig().networks
        bridgeless_ovs_nets = [
            net for net, attrs in six.iteritems(running_networks)
            if attrs['switch'] == 'ovs' and not attrs['bridged']]
        ovs_info.fake_bridgeless(
            ovs_netinfo, _netinfo['nics'], bridgeless_ovs_nets)

        for type, entries in six.iteritems(ovs_netinfo):
            _netinfo[type].update(entries)

    return _netinfo


@memoized
def _is_ovs_service_running():
    return service_status('openvswitch', verbose=False) == 0


def _setup_ipv6autoconf(networks):
    # TODO: Move func to IP or LINK level.
    # TODO: Implicitly disable ipv6 on SB iface/s and fake ifaces (br, bond).
    for net, attrs in six.iteritems(networks):
        if 'remove' in attrs:
            continue
        if attrs['ipv6autoconf']:
            address.enable_ipv6_local_auto(net)
        else:
            address.disable_ipv6_local_auto(net)


# TODO: use this function also for legacy switch
def _split_setup_actions(query, running_entries):
    entries2add = {}
    entries2edit = {}
    entries2remove = {}

    for entry, attrs in six.iteritems(query):
        if 'remove' in attrs:
            entries2remove[entry] = attrs
        elif entry in running_entries:
            entries2edit[entry] = attrs
        else:
            entries2add[entry] = attrs

    return entries2add, entries2edit, entries2remove
