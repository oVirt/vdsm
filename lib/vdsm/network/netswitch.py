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

import itertools

import six

from vdsm.network.ip import address
from vdsm.network.ip import dhclient
from vdsm.network.kernelconfig import KernelConfig
from vdsm.network.libvirt import networks as libvirt_nets
from vdsm.network.link import iface
from vdsm.network.link.bond import Bond
from vdsm.network.link.setup import SetupBonds
from vdsm.network.netinfo.cache import (libvirtNets2vdsm, get as netinfo_get,
                                        CachingNetInfo,
                                        NetInfo)
from vdsm.tool.service import service_status
from vdsm.utils import memoized

from . import connectivity
from . import ifacquire
from . import legacy_switch
from . import errors as ne
from .ovs import info as ovs_info
from .ovs import switch as ovs_switch
from .netconfpersistence import RunningConfig, Transaction


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

            # When removing a network/bond, we try to determine its switch
            # type from the netinfo report.
            # This is not always possible, specifically with bonds owned by ovs
            # but not successfully deployed (not saved in running config).
            if (switch_type == legacy_switch.SWITCH_TYPE and
                    Bond(name).exists() and
                    not legacy_switch.ConfiguratorClass.owned_device(name)):
                # If not owned by Legacy, assume OVS and let it be removed in
                # the OVS way.
                switch_type = ovs_switch.SWITCH_TYPE

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
    _validate_common(networks)

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

    with legacy_switch.ConfiguratorClass(_netinfo,
                                         in_rollback) as configurator:
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
    ovs_nets = ovs_info.create_netinfo(_ovs_info)['networks']
    _netinfo = netinfo()

    nets2add, nets2edit, nets2remove = _split_setup_actions(
        networks, ovs_nets)
    bonds2add, bonds2edit, bonds2remove = _split_setup_actions(
        bondings, _netinfo['bondings'])

    # TODO: If a nework is to be edited, we remove it and recreate again.
    # We should implement editation.
    nets2add.update(nets2edit)
    nets2remove.update(nets2edit)

    # FIXME: we are not able to move a nic from bond to network in one setup
    with Transaction(in_rollback=in_rollback) as config:
        setup_bonds = SetupBonds(bonds2add, bonds2edit, bonds2remove, config)
        with ifacquire.Transaction(ovs_nets) as acq:
            _remove_networks(nets2remove, _ovs_info, config)

            setup_bonds.remove_bonds()

            # Post removal of nets, update ovs_nets.
            ovs_nets = ovs_info.create_netinfo(_ovs_info)['networks']
            ovs_switch.validator.validate_nic_usage(
                nets2add, bonds2add,
                _get_kernel_nets_nics(ovs_nets), _get_kernel_bonds_slaves())

            acq.acquire(setup_bonds.ifaces_for_acquirement)
            setup_bonds.edit_bonds()
            setup_bonds.add_bonds()

            _add_networks(nets2add, _ovs_info, config, acq)

            setup_ipv6autoconf(networks)
            set_ovs_links_up(nets2add, bonds2add, bonds2edit)
            setup_ovs_ip_config(nets2add, nets2remove)

            connectivity.check(options)


def _get_kernel_nets_nics(ovs_networks):
    return {netattr['nics'][0] for netattr in six.itervalues(ovs_networks)
            if netattr['nics'] and not netattr['bond']}


def _get_kernel_bonds_slaves():
    kernel_bonds_slaves = set()
    for bond_name in Bond.bonds():
        kernel_bonds_slaves |= Bond(bond_name).slaves
    return kernel_bonds_slaves


def _remove_networks(nets2remove, ovs_info, config):
    net_rem_setup = ovs_switch.create_network_removal_setup(ovs_info)
    net_rem_setup.remove(nets2remove)
    for net, attrs in six.iteritems(nets2remove):
        config.removeNetwork(net)


def _add_networks(nets2add, ovs_info, config, acq):
    net_add_setup = ovs_switch.create_network_addition_setup(ovs_info)
    with net_add_setup.add(nets2add):
        acq.acquire(net_add_setup.acquired_ifaces)
    for net, attrs in six.iteritems(nets2add):
        config.setNetwork(net, attrs)


def setup_ovs_ip_config(nets2add, nets2remove):
    # TODO: This should be moved to network/api.py when we solve rollback
    # transactions.
    for net in nets2remove:
        _drop_dhcp_config(net)

    for net, attrs in six.iteritems(nets2add):
        sb = attrs.get('bonding') or attrs.get('nic')
        address.disable_ipv6(sb)

        _set_static_ip_config(net, attrs)
        _set_dhcp_config(net, attrs)


def _drop_dhcp_config(iface):
    dhclient.stop(iface)


def _set_dhcp_config(iface, attrs):
    # TODO: DHCPv6
    blocking_dhcp = attrs.get('blockingdhcp', False)
    duid_source = attrs.get('bonding') or attrs.get('nic')

    ipv4 = address.IPv4(*_ipv4_conf_params(attrs))
    if ipv4.bootproto == 'dhcp':
        dhclient.run(iface, 4, ipv4.defaultRoute, duid_source, blocking_dhcp)


def _set_static_ip_config(iface, attrs):
    address.flush(iface)
    ipv4 = address.IPv4(*_ipv4_conf_params(attrs))
    ipv6 = address.IPv6(*_ipv6_conf_params(attrs))
    address.add(iface, ipv4, ipv6)


def _ipv4_conf_params(attrs):
    return (attrs.get('ipaddr'), attrs.get('netmask'), attrs.get('gateway'),
            attrs.get('defaultRoute'), attrs.get('bootproto'))


def _ipv6_conf_params(attrs):
    return (attrs.get('ipv6addr'), attrs.get('ipv6gateway'),
            attrs.get('defaultRoute'), attrs.get('ipv6autoconf'),
            attrs.get('dhcpv6'))


def set_ovs_links_up(nets2add, bonds2add, bonds2edit):
    # TODO: Make this universal for legacy and ovs.
    for dev in _gather_ovs_ifaces(nets2add, bonds2add, bonds2edit):
        iface.up(dev)


def _gather_ovs_ifaces(nets2add, bonds2add, bonds2edit):
    nets_and_bonds = set(
        itertools.chain.from_iterable([nets2add, bonds2add, bonds2edit]))

    nets_nics = {attrs['nic'] for attrs in six.itervalues(nets2add)
                 if 'nic' in attrs}

    bonds_nics = set()
    for bonds in (bonds2add, bonds2edit):
        bond_nics = itertools.chain.from_iterable(
            attrs['nics'] for attrs in six.itervalues(bonds))
        bonds_nics.update(bond_nics)

    return itertools.chain.from_iterable(
        [nets_and_bonds, nets_nics, bonds_nics])


def netinfo(compatibility=None):
    # TODO: Version requests by engine to ease handling of compatibility.
    _netinfo = netinfo_get(compatibility=compatibility)

    if _is_ovs_service_running():
        try:
            ovs_netinfo = ovs_info.get_netinfo()
        except ne.OvsDBConnectionError:
            _is_ovs_service_running.invalidate()
            raise

        running_networks = RunningConfig().networks
        bridgeless_ovs_nets = [
            net for net, attrs in six.iteritems(running_networks)
            if attrs['switch'] == 'ovs' and not attrs['bridged']]
        ovs_info.fake_bridgeless(
            ovs_netinfo, _netinfo, bridgeless_ovs_nets)

        for type, entries in six.iteritems(ovs_netinfo):
            _netinfo[type].update(entries)

        _set_bond_type_by_usage(_netinfo)

    return _netinfo


def _set_bond_type_by_usage(_netinfo):
    """
    Engine uses bond switch type to indicate what switch type implementation
    the bond belongs to (as each is implemented and managed differently).
    In both cases, the bond used is a linux bond.
    Therefore, even though the bond is detected as a 'legacy' one, it is
    examined against the running config for the switch that uses it and updates
    its switch type accordingly.
    """
    for bond, bond_attrs in six.iteritems(RunningConfig().bonds):
        if (bond_attrs['switch'] == ovs_switch.SWITCH_TYPE and
                bond in _netinfo['bondings']):
            _netinfo['bondings'][bond]['switch'] = ovs_switch.SWITCH_TYPE


@memoized
def _is_ovs_service_running():
    return service_status('openvswitch', verbose=False) == 0


def setup_ipv6autoconf(networks):
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


def ovs_net2bridge(network_name):
    if not _is_ovs_service_running():
        return None

    return ovs_info.northbound2bridge(network_name)


def net2vlan(network_name):
    # Using RunningConfig avoids the need to require root access.
    net_attr = RunningConfig().networks.get(network_name)
    return net_attr.get('vlan') if net_attr else None


def switch_type_change_needed(nets, bonds, running_config):
    """
    Check if we have to do switch type change in order to set up requested
    networks and bondings. Note that this functions should be called only
    after canonicalization and verification of the input.
    """
    running = _get_switch_type(running_config.networks, running_config.bonds)
    requested = _get_switch_type(nets, bonds)
    return running and requested and running != requested


def _get_switch_type(nets, bonds):
    """
    Get switch type from nets and bonds validated for switch type change.
    Validation makes sure, that all entries share the same switch type and
    therefore it is possible to return first found switch type.
    """
    for entries in nets, bonds:
        for attrs in six.itervalues(entries):
            if 'remove' not in attrs:
                return attrs['switch']
    return None


def validate_switch_type_change(nets, bonds, running_config):
    for requests in nets, bonds:
        for attrs in six.itervalues(requests):
            if 'remove' in attrs:
                raise ne.ConfigNetworkError(
                    ne.ERR_BAD_PARAMS,
                    'Switch type change request cannot contain removals')

    if frozenset(running_config.networks) != frozenset(nets):
        raise ne.ConfigNetworkError(
            ne.ERR_BAD_PARAMS,
            'All networks must be reconfigured on switch type change')
    if frozenset(running_config.bonds) != frozenset(bonds):
        raise ne.ConfigNetworkError(
            ne.ERR_BAD_PARAMS,
            'All bondings must be reconfigured on switch type change')


def _validate_common(nets):
    _validate_southbound_devices_usages(NetInfo(netinfo()), nets)


def _validate_southbound_devices_usages(ni, nets):
    kernel_config = KernelConfig(ni)

    for requested_net, net_info in six.viewitems(nets):
        if 'remove' in net_info:
            kernel_config.removeNetwork(requested_net)

    for requested_net, net_info in six.viewitems(nets):
        if 'remove' in net_info:
            continue
        kernel_config.setNetwork(requested_net, net_info)

    underlying_devices = []
    for net_attrs in six.viewvalues(kernel_config.networks):
        vlan = net_attrs.get('vlan')
        if 'bonding' in net_attrs:
            underlying_devices.append((net_attrs['bonding'], vlan))
        elif 'nic' in net_attrs:
            underlying_devices.append((net_attrs['nic'], vlan))

    if len(set(underlying_devices)) < len(underlying_devices):
        raise ne.ConfigNetworkError(
            ne.ERR_BAD_PARAMS,
            'multiple networks/similar vlans cannot be'
            ' defined on a single underlying device. '
            'kernel networks: {}\nrequested networks: {}'.format(
                kernel_config.networks,
                nets))
