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

from vdsm.network.libvirt import networks as libvirt_nets
from vdsm.network.netinfo.cache import (libvirtNets2vdsm, get as netinfo_get,
                                        CachingNetInfo)
from .netconfpersistence import RunningConfig

from . import connectivity
from . import legacy_switch
from . import errors as ne
from .ovs import switch as ovs_switch


def _split_switch_type_entries(entries, running_config_entries):
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
            running_attrs = running_config_entries.get(name, {})
            switch_type = running_attrs.get('switch')
        else:
            switch_type = attrs['switch']
        store_entry(name, attrs, switch_type)

    return legacy_entries, ovs_entries


def _split_switch_type(nets, bonds):
    running_config = RunningConfig()
    legacy_nets, ovs_nets = _split_switch_type_entries(
        nets, running_config.networks)
    legacy_bonds, ovs_bonds = _split_switch_type_entries(
        bonds, running_config.bonds)
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
        ovs_switch.validate_network_setup(ovs_nets, legacy_bonds)


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
    with ovs_switch.rollback_trigger(in_rollback):
        ovs_switch.setup(networks, bondings)
        connectivity.check(options)
