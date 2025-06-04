#!/usr/bin/python3

# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import print_function

import os
import sys
import tempfile
import time
import traceback

import checkips_utils
import hooking
from vdsm import constants
import vdsm.network.netconfpersistence as persist_net

HELP_ARG = '-h'
TEST_ARG = '-t'
HELP_TEXT = """usage: %(prog)s [%(help)s] [%(test)s]

Check connectivity from host to specific addresses via ping

optional arguments:
  %(help)s  show this help message and exit
  %(test)s  run a dry test for the hook
""" % {
    'help': HELP_ARG,
    'test': TEST_ARG,
    'prog': sys.argv[0]
}

CONNECTIVITY_TIMEOUT = 60


def _is_network_accessible(net, stats_dir):
    file_path = os.path.join(stats_dir, net)
    if os.path.exists(file_path):
        return (
            time.time() - os.stat(file_path).st_mtime
            <= CONNECTIVITY_TIMEOUT
        )
    return False


def update_networks_state(stats_json, networks, stats_dir):
    for net, net_attrs in networks.items():
        ping_addresses = checkips_utils.get_ping_addresses(net_attrs)
        if ping_addresses and not _is_network_accessible(net, stats_dir):
            net_if = None
            if 'nic' in net_attrs:
                net_if = net_attrs['nic']
            elif 'bonding' in net_attrs:
                net_if = net_attrs['bonding']
            if net_if:
                stats_json['network'][net_if]['state'] = 'down'


def test():
    temp_dir = tempfile.mkdtemp()
    checkips_utils.touch('check_ipv4', temp_dir)
    checkips_utils.touch('check_ipv6', temp_dir)
    try:
        networks = {
            'check_ipv4': {
                'nic': 'eth0_ipv4',
                'custom': {
                    'checkipv4': '127.0.0.1'
                }
            },
            'check_ipv6': {
                'bonding': 'bond_ipv6',
                'custom': {
                    'checkipv6': '::1'
                }
            },
            'check_fqdn': {
                'nic': 'eth_fqdn',
                'custom': {
                    'checkipv4': 'test.test'
                }
            }
        }
        network_stats = {
            'network': {
                'eth0_ipv4': {
                    'state': 'up'
                },
                'bond_ipv6': {
                    'state': 'up'
                },
                'eth_fqdn': {
                    'state': 'up'
                }
            }
        }
        update_networks_state(network_stats, networks, temp_dir)
        expected_states = {
            'eth0_ipv4': 'up',
            'bond_ipv6': 'up',
            'eth_fqdn': 'down'
        }
        for interface, state in expected_states.items():
            test_msg = 'pass'
            if network_stats['network'][interface]['state'] != state:
                test_msg = 'fail'
            print(
                'test %s: interface %s has state %s' %
                (test_msg, interface, state)
            )
    finally:
        os.unlink(os.path.join(temp_dir, 'check_ipv4'))
        os.unlink(os.path.join(temp_dir, 'check_ipv6'))
        os.rmdir(temp_dir)


def main():
    stats_json = hooking.read_json()
    networks = persist_net.PersistentConfig().networks
    update_networks_state(stats_json, networks, constants.P_VDSM_RUN)
    hooking.write_json(stats_json)


if __name__ == '__main__':
    if HELP_ARG in sys.argv:
        hooking.exit_hook(HELP_TEXT)

    try:
        if TEST_ARG in sys.argv:
            test()
        else:
            main()
    except:
        hooking.exit_hook(
            'checkips hook: [unexpected error]: %s\n' %
            traceback.format_exc()
        )
