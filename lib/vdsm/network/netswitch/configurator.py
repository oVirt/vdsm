# Copyright 2016-2019 Red Hat, Inc.
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

import itertools
import logging

import six

from vdsm.common.cache import memoized
from vdsm.common.time import monotonic_time
from vdsm.network import connectivity
from vdsm.network import dns
from vdsm.network import ifacetracking
from vdsm.network import ifacquire
from vdsm.network import legacy_switch
from vdsm.network import errors as ne
from vdsm.network import nmstate
from vdsm.network import sourceroute
from vdsm.network.configurators.ifcfg import Ifcfg, ConfigWriter
from vdsm.network.ip import address
from vdsm.network.ip import dhclient
from vdsm.network.link import dpdk
from vdsm.network.link import nic
from vdsm.network.link.iface import iface as iface_obj
from vdsm.network.netconfpersistence import RunningConfig, Transaction
from vdsm.network.netlink import waitfor
from vdsm.network.ovs import info as ovs_info
from vdsm.network.ovs import switch as ovs_switch
from vdsm.network.link import bond
from vdsm.network.link.setup import SetupBonds
from vdsm.network.netinfo import bridges
from vdsm.network.netinfo.cache import (
    get as netinfo_get,
    CachingNetInfo,
    NetInfo,
)
from vdsm.network.netinfo.cache import get_net_iface_from_config

from . import validator


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
                ne.ERR_BAD_PARAMS, 'Invalid switch type %s' % attrs['switch']
            )

    for name, attrs in six.iteritems(entries):
        if 'remove' in attrs:
            running_attrs = running_entries.get(name, {})
            switch_type = running_attrs.get('switch')

            # When removing a network/bond, we try to determine its switch
            # type from the netinfo report.
            # This is not always possible, specifically with bonds owned by ovs
            # but not successfully deployed (not saved in running config).
            if (
                switch_type == legacy_switch.SWITCH_TYPE
                and bond.Bond(name).exists()
                and not Ifcfg.owned_device(name)
            ):
                # If not owned by Legacy, assume OVS and let it be removed in
                # the OVS way.
                switch_type = ovs_switch.SWITCH_TYPE

        else:
            switch_type = attrs['switch']
        store_entry(name, attrs, switch_type)

    return legacy_entries, ovs_entries


def _split_switch_type(nets, bonds, net_info):
    legacy_nets, ovs_nets = _split_switch_type_entries(
        nets, net_info['networks']
    )
    legacy_bonds, ovs_bonds = _split_switch_type_entries(
        bonds, net_info['bondings']
    )
    return legacy_nets, ovs_nets, legacy_bonds, ovs_bonds


def validate(networks, bondings, net_info):
    legacy_nets, ovs_nets, legacy_bonds, ovs_bonds = _split_switch_type(
        networks, bondings, net_info
    )

    use_legacy_switch = legacy_nets or legacy_bonds
    use_ovs_switch = ovs_nets or ovs_bonds

    if (
        not nmstate.is_nmstate_backend()
        and use_legacy_switch
        and use_ovs_switch
    ):
        raise ne.ConfigNetworkError(
            ne.ERR_BAD_PARAMS,
            'Mixing of legacy and OVS networks is not supported inside one '
            'setupNetworks() call.',
        )

    validator.validate_southbound_devices_usages(networks, NetInfo(net_info))
    validator.validate_network_setup(networks, bondings, net_info)
    if use_legacy_switch:
        legacy_switch.validate_network_setup(legacy_nets)


def setup(networks, bondings, options, net_info, in_rollback):
    if nmstate.is_nmstate_backend():
        _setup_nmstate(networks, bondings, options, in_rollback)
    else:
        _setup(networks, bondings, options, in_rollback, net_info)

    if options.get('commitOnSuccess'):
        persist()


def persist():
    ConfigWriter.clearBackups()
    RunningConfig.store()


def _setup(networks, bondings, options, in_rollback, net_info):
    legacy_nets, ovs_nets, legacy_bonds, ovs_bonds = _split_switch_type(
        networks, bondings, net_info
    )
    use_legacy_switch = legacy_nets or legacy_bonds
    use_ovs_switch = ovs_nets or ovs_bonds
    if use_legacy_switch:
        _setup_legacy(
            legacy_nets, legacy_bonds, options, net_info, in_rollback
        )
    elif use_ovs_switch:
        _setup_ovs(ovs_nets, ovs_bonds, options, net_info, in_rollback)


def _setup_nmstate(networks, bondings, options, in_rollback):
    """
    Setup the networks using nmstate as the backend provider.
    nmstate handles the rollback by itself in case of an error during the
    transaction or in case the resulted state does not include the desired one.

    In order to support the connectivity check, the "regular" rollback is
    used (the Transaction context).
    """
    logging.info('Processing setup through nmstate')
    desired_state = nmstate.generate_state(networks, bondings)
    logging.info('Desired state: %s', desired_state)
    _setup_dynamic_src_routing(networks)
    nmstate.setup(desired_state, verify_change=not in_rollback)

    with Transaction(in_rollback=in_rollback) as config:
        for net_name, net_attrs in six.viewitems(networks):
            if net_attrs.get('remove'):
                config.removeNetwork(net_name)
        for net_name, net_attrs in six.viewitems(networks):
            if not net_attrs.get('remove'):
                config.setNetwork(net_name, net_attrs)
        for bond_name, bond_attrs in six.viewitems(bondings):
            if bond_attrs.get('remove'):
                config.removeBonding(bond_name)
        for bond_name, bond_attrs in six.viewitems(bondings):
            if not bond_attrs.get('remove'):
                config.setBonding(bond_name, bond_attrs)
        _setup_static_src_routing(networks)
        connectivity.check(options)


def _setup_static_src_routing(networks):
    for net_name, net_attrs in six.viewitems(networks):
        gateway = net_attrs.get('gateway')
        if gateway:
            ip_address = net_attrs.get('ipaddr')
            netmask = net_attrs.get('netmask')
            next_hop = get_next_hop_interface(net_name, net_attrs)
            sourceroute.add(next_hop, ip_address, netmask, gateway)


def get_next_hop_interface(net_name, net_attributes):
    if net_attributes.get('bridged'):
        return net_name
    else:
        vlan = net_attributes.get('vlan')
        next_hop_base_iface = net_attributes.get('nic') or net_attributes.get(
            'bonding'
        )
        return (
            next_hop_base_iface
            if not vlan
            else '{}.{}'.format(next_hop_base_iface, vlan)
        )


def _setup_dynamic_src_routing(networks):
    for net_name, net_attrs in six.viewitems(networks):
        is_remove = net_attrs.get('remove', False)
        is_dynamic = net_attrs.get('bootproto') == 'dhcp'
        if is_dynamic and not is_remove:
            ifacetracking.add(_get_network_iface(net_name, net_attrs))


def _get_network_iface(net_name, net_attrs):
    bridged = net_attrs.get('bridged')
    vlan = net_attrs.get('vlan')
    nic = net_attrs.get('nic')
    bond = net_attrs.get('bonding')
    base_iface = nic or bond
    return (
        net_name
        if bridged
        else '{}.{}'.format(base_iface, vlan)
        if vlan
        else base_iface
    )


def _setup_legacy(networks, bondings, options, net_info, in_rollback):
    _netinfo = CachingNetInfo(net_info)

    with Ifcfg(_netinfo, in_rollback) as configurator:
        # from this point forward, any exception thrown will be handled by
        # Configurator.__exit__.

        legacy_switch.remove_networks(
            networks, bondings, configurator, _netinfo
        )

        legacy_switch.bonds_setup(
            bondings, configurator, _netinfo, in_rollback
        )

        legacy_switch.add_missing_networks(
            configurator, networks, bondings, _netinfo
        )

        connectivity.check(options)


def _setup_ovs(networks, bondings, options, net_info, in_rollback):
    _ovs_info = ovs_info.OvsInfo()
    ovs_nets = ovs_info.create_netinfo(_ovs_info)['networks']

    nets2add, nets2edit, nets2remove = _split_setup_actions(networks, ovs_nets)
    bonds2add, bonds2edit, bonds2remove = _split_setup_actions(
        bondings, net_info['bondings']
    )

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
            kernel_bonds = bond.Bond.bonds()
            validator.validate_nic_usage(
                nets2add,
                bonds2add,
                _get_kernel_nets_nics(ovs_nets, kernel_bonds),
                _get_kernel_bonds_slaves(kernel_bonds),
            )

            acq.acquire(setup_bonds.ifaces_for_acquirement)
            setup_bonds.edit_bonds()
            setup_bonds.add_bonds()

            _add_networks(nets2add, _ovs_info, config, acq)

            ovs_switch.update_network_to_bridge_mappings(ovs_info.OvsInfo())

            setup_ipv6autoconf(networks)
            set_ovs_links_up(nets2add, bonds2add, bonds2edit)
            setup_ovs_ip_config(nets2add, nets2remove)

            _setup_ovs_dns(nets2add)

            connectivity.check(options)


def _get_kernel_nets_nics(ovs_networks, kernel_bonds):
    return {
        netattr['southbound']
        for netattr in six.itervalues(ovs_networks)
        if netattr['southbound'] not in kernel_bonds
    }


def _get_kernel_bonds_slaves(kernel_bonds):
    kernel_bonds_slaves = set()
    for bond_name in kernel_bonds:
        kernel_bonds_slaves |= bond.Bond(bond_name).slaves
    return kernel_bonds_slaves


def _remove_networks(nets2remove, ovs_info, config):
    logging.debug('Removing networks: %s', list(nets2remove))
    net_rem_setup = ovs_switch.NetsRemovalSetup(ovs_info)
    net_rem_setup.prepare_setup(nets2remove)
    net_rem_setup.commit_setup()
    for net, attrs in six.iteritems(nets2remove):
        config.removeNetwork(net)


def _add_networks(nets2add, ovs_info, config, acq):
    logging.debug('Adding networks: %s', list(nets2add))
    net_add_setup = ovs_switch.NetsAdditionSetup(ovs_info)
    net_add_setup.prepare_setup(nets2add)
    acq.acquire(net_add_setup.acquired_ifaces)
    net_add_setup.commit_setup()
    for net, attrs in six.iteritems(nets2add):
        config.setNetwork(net, attrs)


def setup_ovs_ip_config(nets2add, nets2remove):
    # TODO: This should be moved to network/api.py when we solve rollback
    # transactions.
    for net in nets2remove:
        _drop_dhcp_config(net)

    for net, attrs in six.iteritems(nets2add):
        sb = attrs.get('bonding') or attrs.get('nic')
        if not dpdk.is_dpdk(sb):
            address.disable_ipv6(sb)

        _set_static_ip_config(net, attrs)
        _set_dhcp_config(net, attrs)


def _drop_dhcp_config(iface):
    dhclient.stop(iface, 4)
    dhclient.stop(iface, 6)


def _set_dhcp_config(iface, attrs):
    blocking_dhcp = attrs.get('blockingdhcp', False)
    duid_source = attrs.get('bonding') or attrs.get('nic')

    ipv4 = address.IPv4(*_ipv4_conf_params(attrs))
    if ipv4.bootproto == 'dhcp':
        dhclient.run(iface, 4, ipv4.defaultRoute, duid_source, blocking_dhcp)

    ipv6 = address.IPv6(*_ipv6_conf_params(attrs))
    if ipv6.dhcpv6:
        dhclient.run(iface, 6, ipv6.defaultRoute, duid_source, blocking_dhcp)


def _set_static_ip_config(iface, attrs):
    address.flush(iface)
    ipv4 = address.IPv4(*_ipv4_conf_params(attrs))
    ipv6 = address.IPv6(*_ipv6_conf_params(attrs))
    address.add(iface, ipv4, ipv6)


def _ipv4_conf_params(attrs):
    return (
        attrs.get('ipaddr'),
        attrs.get('netmask'),
        attrs.get('gateway'),
        attrs.get('defaultRoute'),
        attrs.get('bootproto'),
    )


def _ipv6_conf_params(attrs):
    return (
        attrs.get('ipv6addr'),
        attrs.get('ipv6gateway'),
        attrs.get('defaultRoute'),
        attrs.get('ipv6autoconf'),
        attrs.get('dhcpv6'),
    )


def set_ovs_links_up(nets2add, bonds2add, bonds2edit):
    # TODO: Make this universal for legacy and ovs.
    for dev in _gather_ovs_ifaces(nets2add, bonds2add, bonds2edit):
        iface_obj(dev).up()


def ovs_add_vhostuser_port(bridge, port, socket_path):
    ovs_switch.add_vhostuser_port(bridge, port, socket_path)


def ovs_remove_port(bridge, port):
    ovs_switch.remove_port(bridge, port)


def _gather_ovs_ifaces(nets2add, bonds2add, bonds2edit):
    nets_and_bonds = set(
        itertools.chain.from_iterable([nets2add, bonds2add, bonds2edit])
    )

    nets_nics = {
        attrs['nic'] for attrs in six.itervalues(nets2add) if 'nic' in attrs
    }

    bonds_nics = set()
    for bonds in (bonds2add, bonds2edit):
        bond_nics = itertools.chain.from_iterable(
            attrs['nics'] for attrs in six.itervalues(bonds)
        )
        bonds_nics.update(bond_nics)

    return itertools.chain.from_iterable(
        [nets_and_bonds, nets_nics, bonds_nics]
    )


def netcaps(compatibility):
    net_caps = netinfo(compatibility=compatibility)
    _add_speed_device_info(net_caps)
    _add_bridge_opts(net_caps)
    return net_caps


def netinfo(vdsmnets=None, compatibility=None):
    # TODO: Version requests by engine to ease handling of compatibility.
    _netinfo = netinfo_get(vdsmnets, compatibility)

    if _is_ovs_service_running():
        try:
            ovs_netinfo = ovs_info.get_netinfo()
        except ne.OvsDBConnectionError:
            _is_ovs_service_running.invalidate()
            raise

        running_networks = RunningConfig().networks
        bridgeless_ovs_nets = [
            net
            for net, attrs in six.iteritems(running_networks)
            if attrs['switch'] == 'ovs' and not attrs['bridged']
        ]
        ovs_info.fake_bridgeless(ovs_netinfo, _netinfo, bridgeless_ovs_nets)

        for type, entries in six.iteritems(ovs_netinfo):
            _netinfo[type].update(entries)

        _set_bond_type_by_usage(_netinfo)

    return _netinfo


def _add_speed_device_info(net_caps):
    """Collect and include device speed information in the report."""
    timeout = 2
    for devname, devattr in six.viewitems(net_caps['nics']):
        timeout -= _wait_for_link_up(devname, timeout)
        devattr['speed'] = nic.speed(devname)

    for devname, devattr in six.viewitems(net_caps['bondings']):
        timeout -= _wait_for_link_up(devname, timeout)
        devattr['speed'] = bond.speed(devname)


def _wait_for_link_up(devname, timeout):
    """
    Waiting for link-up, no longer than the specified timeout period.
    The time waited (in seconds) is returned.
    """
    if timeout > 0 and not iface_obj(devname).is_oper_up():
        time_start = monotonic_time()
        with waitfor.waitfor_linkup(devname, timeout=timeout):
            pass
        return monotonic_time() - time_start
    return 0


def _add_bridge_opts(net_caps):
    for bridgename, bridgeattr in six.viewitems(net_caps['bridges']):
        bridgeattr['opts'] = bridges.bridge_options(bridgename)


def _set_bond_type_by_usage(_netinfo):
    """
    Engine uses bond switch type to indicate what switch type implementation
    the bond belongs to (as each is implemented and managed differently).
    In both cases, the bond used is a linux bond.
    Therefore, even though the bond is detected as a 'legacy' one, it is
    examined against the running config for the switch that uses it and updates
    its switch type accordingly.
    """
    for bond_name, bond_attrs in six.iteritems(RunningConfig().bonds):
        if (
            bond_attrs['switch'] == ovs_switch.SWITCH_TYPE
            and bond_name in _netinfo['bondings']
        ):
            _netinfo['bondings'][bond_name]['switch'] = ovs_switch.SWITCH_TYPE


@memoized
def _is_ovs_service_running():
    return ovs_info.is_ovs_service_running()


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

    return ovs_info.bridge_info(network_name)


def net2northbound(network_name):
    nb_device = network_name

    # Using RunningConfig avoids the need to require root access.
    net_attr = RunningConfig().networks.get(network_name)
    is_legacy = net_attr['switch'] == legacy_switch.SWITCH_TYPE
    if not net_attr['bridged'] and is_legacy:
        nb_device = get_net_iface_from_config(network_name, net_attr)

    return nb_device


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
                    'Switch type change request cannot contain removals',
                )

    if frozenset(running_config.networks) != frozenset(nets):
        raise ne.ConfigNetworkError(
            ne.ERR_BAD_PARAMS,
            'All networks must be reconfigured on switch type change',
        )
    if frozenset(running_config.bonds) != frozenset(bonds):
        raise ne.ConfigNetworkError(
            ne.ERR_BAD_PARAMS,
            'All bondings must be reconfigured on switch type change',
        )


def _setup_ovs_dns(nets):
    net_attrs = _lookup_default_route_net(nets)
    if not net_attrs:
        return

    if net_attrs.get('bootproto') == 'dhcp' or net_attrs.get('dhcpv6'):
        # TODO Support for scenario when DHCP client overwrittes our
        # static configuration.
        # That would mean to add support for custom dhclient config to place
        # our DNS settings over dhclient
        return

    nameservers = net_attrs.get('nameservers')
    if nameservers:
        dns.add_host_nameservers(nameservers)


def _lookup_default_route_net(nets):
    # If not found, returns {}
    # Otherwise network_attr
    for net, attrs in six.iteritems(nets):
        if attrs.get('defaultRoute'):
            return attrs
    return {}
