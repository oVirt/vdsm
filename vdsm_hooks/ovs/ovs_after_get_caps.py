#!/usr/bin/env python
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

from vdsm.netconfpersistence import RunningConfig
from vdsm.netinfo import dhcp, routes as netinfo_routes, addresses, mtus

from hooking import execCmd
import hooking

from ovs_utils import (get_bond_options, iter_ovs_nets, iter_ovs_bonds,
                       EXT_OVS_APPCTL, EXT_OVS_VSCTL, BRIDGE_NAME)


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


def _get_net_info(attrs, interface, dhcpv4ifaces, dhcpv6ifaces, routes):
    mtu = mtus.getMtu(interface)
    addr, netmask, ipv4addrs, ipv6addrs = addresses.getIpInfo(interface)
    dhcpv4 = dhcp.dhcp_used(interface, dhcpv4ifaces, attrs)
    dhcpv6 = dhcp.dhcp_used(interface, dhcpv6ifaces, attrs, family=6)
    gateway = routes.get_gateway(routes, interface)
    ipv6gateway = routes.get_gateway(routes, interface, family=6)
    return {
        'mtu': str(mtu),
        'addr': addr,
        'gateway': gateway,
        'netmask': netmask,
        'dhcpv4': dhcpv4,
        'ipv4addrs': ipv4addrs,
        'ipv6addrs': ipv6addrs,
        'ipv6gateway': ipv6gateway,
        'dhcpv6': dhcpv6,
        'cfg': {'BOOTPROTO': 'dhcp' if dhcpv4 else 'none'}}


def networks_caps(running_config):
    ovs_networks_caps = {}
    dhcpv4ifaces, dhcpv6ifaces = dhcp.get_dhclient_ifaces()
    routes = netinfo_routes.get_routes()
    for network, attrs in iter_ovs_nets(running_config.networks):
        interface = network if 'vlan' in attrs else BRIDGE_NAME
        net_info = _get_net_info(attrs, interface, dhcpv4ifaces, dhcpv6ifaces,
                                 routes)
        net_info['iface'] = network
        net_info['bridged'] = True
        net_info['ports'] = _list_ports(interface)
        net_info['stp'] = _get_stp(interface)
        ovs_networks_caps[network] = net_info
    return ovs_networks_caps


def bridges_caps(running_config):
    ovs_bridges_caps = {}
    dhcpv4ifaces, dhcpv6ifaces = dhcp.get_dhclient_ifaces()
    routes = netinfo_routes.get_routes()
    for network, attrs in iter_ovs_nets(running_config.networks):
        interface = network if 'vlan' in attrs else BRIDGE_NAME
        net_info = _get_net_info(attrs, interface, dhcpv4ifaces, dhcpv6ifaces,
                                 routes)
        net_info['bridged'] = True
        net_info['ports'] = _list_ports(interface)
        # TODO netinfo._bridge_options does not work here
        net_info['opts'] = {}
        net_info['stp'] = _get_stp(interface)
        ovs_bridges_caps[network] = net_info
    return ovs_bridges_caps


def vlans_caps(running_config):
    ovs_vlans_caps = {}
    dhcpv4ifaces, dhcpv6ifaces = dhcp.get_dhclient_ifaces()
    routes = netinfo_routes.get_routes()
    for network, attrs in iter_ovs_nets(running_config.networks):
        vlan = attrs.get('vlan')
        if vlan is not None:
            net_info = _get_net_info(attrs, network, dhcpv4ifaces,
                                     dhcpv6ifaces, routes)
            iface = attrs.get('bonding') or attrs.get('nic')
            net_info['iface'] = iface
            net_info['bridged'] = True
            net_info['vlanid'] = vlan
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
    dhcpv4ifaces, dhcpv6ifaces = dhcp.get_dhclient_ifaces()
    routes = netinfo_routes.get_routes()
    for bonding, attrs in iter_ovs_bonds(running_config.bonds):
        options = get_bond_options(attrs.get('options'), keep_custom=True)
        net_info = _get_net_info(attrs, bonding, dhcpv4ifaces, dhcpv6ifaces,
                                 routes)
        net_info['slaves'] = attrs.get('nics')
        net_info['active_slave'] = _get_active_slave(bonding)
        net_info['opts'] = options
        ovs_bonding_caps[bonding] = net_info
    return ovs_bonding_caps


def main():
    running_config = RunningConfig()
    caps = hooking.read_json()
    caps['networks'].update(networks_caps(running_config))
    caps['bridges'].update(bridges_caps(running_config))
    caps['vlans'].update(vlans_caps(running_config))
    caps['bondings'].update(bondings_caps(running_config))
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
