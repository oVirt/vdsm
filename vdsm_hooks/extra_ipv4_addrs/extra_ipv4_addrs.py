#!/usr/bin/python3

# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import print_function
import sys
import hooking
import traceback

from vdsm.network import ipwrapper
from vdsm.network import netinfo
from vdsm.network import netswitch


def test():
    options = 'addr/prefix,\taddr2/prefix2,addrN/prefixN '
    commands = ['/sbin/ip -4 addr add dev super_device addr/prefix',
                '/sbin/ip -4 addr add dev super_device addr2/prefix2',
                '/sbin/ip -4 addr add dev super_device addrN/prefixN']
    print(commands)
    assert list(' '.join(cmd) for cmd in _generate_commands(
        options, 'super_device')) == commands


def main():
    """Read ipv4_addrs from the network 'custom' properties and apply them
    to the network's top device"""
    setup_nets_config = hooking.read_json()
    for network, attrs in setup_nets_config['request']['networks'].items():
        if 'remove' in attrs:
            continue

        if 'custom' in attrs:
            _process_network(network, attrs)


def _process_network(network, attrs):
    """Applies extra ipv4 addresses to the network if necessary"""
    options = attrs['custom'].get('ipv4_addrs')
    if options is not None:
        top_dev = _top_dev(network, attrs)
        for cmd in _generate_commands(options, top_dev):
            hooking.execCmd(cmd, sudo=True)


def _generate_commands(options, top_level_device):
    for addr in options.split(','):
        yield [ipwrapper._IP_BINARY.cmd, '-4', 'addr', 'add', 'dev',
               top_level_device, addr.strip()]


def _top_dev(network, attrs):
    if hooking.tobool(attrs.get('bridged')):
        return network
    # bridgeless
    nics, vlan, _, bonding = netinfo.cache.NetInfo(
        netswitch.configurator.netinfo()).getNicsVlanAndBondingForNetwork(
            network)
    return vlan or bonding or nics[0]


if __name__ == '__main__':
    try:
        if '--test' in sys.argv:
            test()
        else:
            main()
    except:
        hooking.exit_hook('extra ipv4 addrs hook: [unexpected error]: %s\n' %
                          traceback.format_exc())
