# Copyright 2016-2020 Red Hat, Inc.
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

import logging

import six

from vdsm.common.cache import memoized
from vdsm.common.time import monotonic_time
from vdsm.network import connectivity
from vdsm.network import errors as ne
from vdsm.network import nmstate
from vdsm.network import sourceroute
from vdsm.network.common import switch_util as util
from vdsm.network.configurators import qos
from vdsm.network.dhcp_monitor import MonitoredItemPool
from vdsm.network.link import nic
from vdsm.network.link import setup as link_setup
from vdsm.network.link.iface import iface as iface_obj
from vdsm.network.netconfpersistence import RunningConfig, Transaction
from vdsm.network.netlink import waitfor
from vdsm.network.ovs import info as ovs_info
from vdsm.network.ovs import switch as ovs_switch
from vdsm.network.link import bond
from vdsm.network.netinfo import bridges
from vdsm.network.netinfo.cache import get as netinfo_get, NetInfo
from vdsm.network.netinfo.cache import get_net_iface_from_config

from . import validator


def validate(networks, bondings, net_info, running_config):
    ovs_nets, legacy_nets = util.split_switch_type(
        networks, running_config.networks
    )
    ovs_bonds, legacy_bonds = util.split_switch_type(
        bondings, running_config.bonds
    )

    use_legacy_switch = legacy_nets or legacy_bonds
    use_ovs_switch = ovs_nets or ovs_bonds

    if use_legacy_switch and use_ovs_switch:
        raise ne.ConfigNetworkError(
            ne.ERR_BAD_PARAMS,
            'Mixing of legacy and OVS networks is not supported inside one '
            'setupNetworks() call.',
        )

    validator.validate_network_setup(networks, bondings, net_info)
    if use_legacy_switch:
        validator.validate_legacy_network_setup(legacy_nets)


def setup(networks, bondings, options, in_rollback):
    _setup_nmstate(networks, bondings, options, in_rollback)

    if options.get('commitOnSuccess'):
        persist()


def persist():
    RunningConfig.store()


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
    net_info = NetInfo(netinfo_get())

    with Transaction(in_rollback=in_rollback, persistent=False) as config:
        _setup_qos(networks, net_info, config.networks)
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
        config.save()
        link_setup.setup_custom_bridge_opts(networks)
        connectivity.check(options)


def _setup_static_src_routing(networks):
    for net_name, net_attrs in six.viewitems(networks):
        gateway = net_attrs.get('gateway')
        if gateway:
            ip_address = net_attrs.get('ipaddr')
            netmask = net_attrs.get('netmask')
            next_hop = _get_network_iface(net_name, net_attrs)
            sourceroute.remove(next_hop)
            sourceroute.add(next_hop, ip_address, netmask, gateway)


def _setup_qos(networks, net_info, rnetworks):
    for net_name, net_attrs in _order_networks(networks):
        rnet_attrs = rnetworks.get(net_name, {})
        out = _get_qos_out(net_attrs)
        rout = _get_qos_out(rnet_attrs)

        if net_attrs.get('remove') or (rout and not out):
            _remove_qos(rnet_attrs, net_info)
        elif out:
            _configure_qos(net_attrs, out)


def _get_qos_out(net_attrs):
    return net_attrs.get('hostQos', {}).get('out')


def _setup_dynamic_src_routing(networks):
    pool = MonitoredItemPool.instance()
    for net_name, net_attrs in six.viewitems(networks):
        is_remove = net_attrs.get('remove', False)
        is_dhcpv4 = net_attrs.get('bootproto') == 'dhcp'
        is_dhcpv6 = net_attrs.get('dhcpv6', False)
        iface = _get_network_iface(net_name, net_attrs)
        if is_remove:
            continue

        if is_dhcpv4:
            pool.add((iface, 4))
        if is_dhcpv6:
            pool.add((iface, 6))


def _configure_qos(net_attrs, out):
    vlan = net_attrs.get('vlan')
    base_iface = _get_base_iface(net_attrs)
    qos.configure_outbound(out, base_iface, vlan)


def _remove_qos(net_attrs, net_info):
    vlan = net_attrs.get('vlan')
    base_iface = _get_base_iface(net_attrs)
    if (
        base_iface in net_info.nics
        or base_iface in net_info.bondings
        and net_attrs.get('hostQos')
    ):
        qos.remove_outbound(base_iface, vlan, net_info)


def _get_network_iface(net_name, net_attrs):
    switch = net_attrs.get('switch')
    if switch == util.SwitchType.OVS or net_attrs.get('bridged'):
        return net_name

    vlan = net_attrs.get('vlan')
    base_iface = _get_base_iface(net_attrs)
    return '{}.{}'.format(base_iface, vlan) if vlan else base_iface


def _get_base_iface(net_attrs):
    return net_attrs.get('nic') or net_attrs.get('bonding')


def _order_networks(networks):
    vlanned_nets = (
        (net, attr) for net, attr in six.viewitems(networks) if 'vlan' in attr
    )
    non_vlanned_nets = (
        (net, attr)
        for net, attr in six.viewitems(networks)
        if 'vlan' not in attr
    )

    for net, attr in vlanned_nets:
        yield net, attr
    for net, attr in non_vlanned_nets:
        yield net, attr


def ovs_add_vhostuser_port(bridge, port, socket_path):
    ovs_switch.add_vhostuser_port(bridge, port, socket_path)


def ovs_remove_port(bridge, port):
    ovs_switch.remove_port(bridge, port)


def netcaps(compatibility):
    net_caps = netinfo(compatibility=compatibility)
    _add_speed_device_info(net_caps)
    _add_bridge_opts(net_caps)
    return net_caps


def netinfo(vdsmnets=None, compatibility=None):
    # TODO: Version requests by engine to ease handling of compatibility.
    running_config = RunningConfig()
    _netinfo = netinfo_get(vdsmnets, compatibility)
    if _is_ovs_service_running():
        state = nmstate.state_show()
        nmstate.ovs_netinfo(_netinfo, running_config.networks, state)
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


def ovs_net2bridge(network_name):
    if not _is_ovs_service_running():
        return None

    return ovs_info.bridge_info(network_name)


def net2northbound(network_name):
    nb_device = network_name

    # Using RunningConfig avoids the need to require root access.
    net_attr = RunningConfig().networks.get(network_name)
    is_legacy = net_attr['switch'] == util.SwitchType.LINUX_BRIDGE
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
