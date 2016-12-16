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
import re
import sys
import traceback

from vdsm.network.ip import dhclient
from vdsm.network.netconfpersistence import RunningConfig
from vdsm.network.netinfo import routes as netinfo_routes, addresses, mtus
from vdsm.network.netinfo.bonding import parse_bond_options

from hooking import execCmd
import hooking

from ovs_utils import (iter_ovs_nets, iter_ovs_bonds, EXT_OVS_APPCTL,
                       EXT_OVS_VSCTL, BRIDGE_NAME)


def _get_stp(iface):
    if iface != BRIDGE_NAME:
        return 'off'

    rc, out, err = execCmd([EXT_OVS_VSCTL, 'get', 'Bridge', BRIDGE_NAME,
                            'stp_enable'], sudo=True)
    if rc != 0:
        hooking.exit_hook('\n'.join(err))
    if out[0] == 'true':
        return 'on'
    else:
        return 'off'


def _list_ports(bridge):
    rc, out, err = execCmd([EXT_OVS_VSCTL, 'list-ports', bridge], sudo=True)
    if rc != 0:
        hooking.exit_hook('\n'.join(err))
    return out


def _get_net_info(interface, routes):
    mtu = mtus.getMtu(interface)
    ipaddrs = addresses.getIpAddrs()
    addr, netmask, ipv4addrs, ipv6addrs = addresses.getIpInfo(interface,
                                                              ipaddrs)
    dhcpv4 = dhclient.is_active(interface, family=4)
    dhcpv6 = dhclient.is_active(interface, family=6)
    gateway = netinfo_routes.get_gateway(routes, interface)
    ipv6gateway = netinfo_routes.get_gateway(routes, interface, family=6)

    return {
        'mtu': str(mtu),
        'addr': addr,
        'gateway': gateway,
        'netmask': netmask,
        'dhcpv4': dhcpv4,
        'ipv4addrs': ipv4addrs,
        'ipv6addrs': ipv6addrs,
        'ipv6autoconf': addresses.is_ipv6_local_auto(interface),
        'ipv6gateway': ipv6gateway,
        'dhcpv6': dhcpv6,
        'ipv4defaultroute': netinfo_routes.is_default_route(gateway)}


def _get_ports(network, attrs):
    """Return network's ports. Such ports consist of a port explicitly assigned
    during network setup (nic/bond/vlan) and ports attached by libvirt.
    Network's nics and bonds are listed by ovs-vsctl list-ports command.
    However, if assigned port is a vlan, it is not listed and it has to be
    added explicitly."""
    top_device = network if 'vlan' in attrs else BRIDGE_NAME
    ports = _list_ports(top_device)
    if 'vlan' in attrs:
        assigned_vlan = '%s.%s' % (attrs.get('bonding') or attrs.get('nic'),
                                   attrs.get('vlan'))
        ports.append(assigned_vlan)
    return ports


def networks_caps(running_config):

    def get_engine_expected_top_dev(net, attrs):
        """Return top device (iface) expected by Engine."""
        nic_bond = attrs.get('bonding') or attrs.get('nic')
        vlan = attrs.get('vlan')
        return (net if attrs.get('bridged', True)
                else '%s.%s' % (nic_bond, vlan) if vlan is not None
                else nic_bond)

    ovs_networks_caps = {}
    routes = netinfo_routes.get_routes()
    for network, attrs in iter_ovs_nets(running_config.networks):
        actual_top_dev = network if 'vlan' in attrs else BRIDGE_NAME
        expected_top_dev = get_engine_expected_top_dev(network, attrs)
        net_info = _get_net_info(actual_top_dev, routes)
        net_info['iface'] = expected_top_dev
        # report the network to be bridgeless if this is what Engine expects
        net_info['bridged'] = attrs.get('bridged')
        net_info['ports'] = _get_ports(network, attrs)
        net_info['stp'] = _get_stp(actual_top_dev)
        ovs_networks_caps[network] = net_info
    return ovs_networks_caps


def bridges_caps(running_config):
    ovs_bridges_caps = {}
    routes = netinfo_routes.get_routes()
    for network, attrs in iter_ovs_nets(running_config.networks):
        # report the network to be bridgeless if this is what Engine expects
        if attrs.get('bridged'):
            interface = network if 'vlan' in attrs else BRIDGE_NAME
            net_info = _get_net_info(interface, routes)
            net_info['bridged'] = True
            net_info['ports'] = _get_ports(network, attrs)
            # TODO netinfo._bridge_options does not work here
            net_info['opts'] = {}
            net_info['stp'] = _get_stp(interface)
            ovs_bridges_caps[network] = net_info
    return ovs_bridges_caps


def vlans_caps(running_config):
    ovs_vlans_caps = {}
    routes = netinfo_routes.get_routes()
    for network, attrs in iter_ovs_nets(running_config.networks):
        vlan = attrs.get('vlan')
        if vlan is not None:
            net_info = _get_net_info(network, routes)
            iface = attrs.get('bonding') or attrs.get('nic')
            net_info['iface'] = iface
            net_info['bridged'] = attrs.get('bridged')
            net_info['vlanid'] = int(vlan)
            ovs_vlans_caps['%s.%s' % (iface, vlan)] = net_info
    return ovs_vlans_caps


def _get_active_slave(bonding):
    """ Get OVS bond's active slave if there is any. Match:
    slave iface_name: enabled
        active slave

    TODO: since openvswitch 2.3.1, active slave is also listed in header of
    command output.
    """
    rc, out, err = execCmd([EXT_OVS_APPCTL, 'bond/show', bonding],
                           sudo=True)
    if rc != 0:
        hooking.exit_hook('\n'.join(err))
    active_slave_regex = re.compile('\nslave (.*):.*\n.*active slave\n')
    active_slaves = active_slave_regex.findall('\n'.join(out))
    active_slave = active_slaves[0] if len(active_slaves) > 0 else ''
    return active_slave


def bondings_caps(running_config):
    ovs_bonding_caps = {}
    routes = netinfo_routes.get_routes()
    for bonding, attrs in iter_ovs_bonds(running_config.bonds):
        options = parse_bond_options(attrs.get('options'), keep_custom=True)
        net_info = _get_net_info(bonding, routes)
        net_info['slaves'] = attrs.get('nics')
        net_info['active_slave'] = _get_active_slave(bonding)
        net_info['opts'] = options
        ovs_bonding_caps[bonding] = net_info
    return ovs_bonding_caps


def _update_expected_ip_info(caps, running_config):
    """
    If a network is marked as bridgeless and untagged, we have to report its IP
    info on attached nic/bond.
    """

    def copy_net_info(source, destination):
        KEYS = {'addr', 'gateway', 'netmask', 'dhcpv4', 'ipv4addrs',
                'ipv6addrs', 'ipv6autoconf', 'ipv6gateway', 'dhcpv6'}
        for key in KEYS:
            destination[key] = source[key]

    for network, attrs in iter_ovs_nets(running_config.networks):
        if not attrs.get('bridged', True) and 'vlan' not in attrs:
            bond = attrs.get('bond')
            nic = attrs.get('nic')
            if bond is not None:
                copy_net_info(
                    caps['networks'][network], caps['bondings'][bond])
            elif nic is not None:
                copy_net_info(caps['networks'][network], caps['nics'][nic])


def main():
    running_config = RunningConfig()
    caps = hooking.read_json()
    caps['networks'].update(networks_caps(running_config))
    caps['bridges'].update(bridges_caps(running_config))
    caps['vlans'].update(vlans_caps(running_config))
    caps['bondings'].update(bondings_caps(running_config))
    _update_expected_ip_info(caps, running_config)
    hooking.write_json(caps)


def test_get_slaves():
    def fake_execCmd(command, sudo=False):
        ovs_appctl_output = [
            '---- bond1 ----\n',
            'bond_mode: active-backup\n',
            'bond-hash-basis: 0\n',
            'updelay: 0 ms\n',
            'downdelay: 0 ms\n',
            'lacp_status: off\n',
            '\n',
            'slave eth0: enabled\n',
            '\tactive slave\n',
            '\tmay_enable: true\n',
            '\n',
            'slave eth1: enabled\n',
            '\tmay_enable: true\n']
        return 0, ovs_appctl_output, []
    global execCmd
    execCmd = fake_execCmd

    active_slave = _get_active_slave('bond1')
    assert active_slave == 'eth0'


if __name__ == '__main__':
    try:
        # Usage: PYTHONPATH=vdsm:vdsm/vdsm ./ovs_after_get_caps.py -t
        if '-t' in sys.argv:
            test_get_slaves()
        else:
            main()
    except:
        hooking.exit_hook(traceback.format_exc())
