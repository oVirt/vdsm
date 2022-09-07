#!/usr/bin/python3

# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import print_function
from collections import namedtuple
import sys
import hooking
import traceback

from vdsm.common.cmdutils import CommandPath
from vdsm.network.link.bond import Bond

ETHTOOL_BINARY = CommandPath(
    'ethtool',
    '/usr/sbin/ethtool',  # F19+
    '/sbin/ethtool',  # EL6, ubuntu and Debian
    '/usr/bin/ethtool',  # Arch
)

ALL_SLAVES = '*'  # wildcard to make the hook resolve the nics to modify

Subcommand = namedtuple('Subcommand', ('name', 'device', 'flags'))


class EthtoolError(Exception):
    pass


def _test_cmd_with_nics(nics, ethtool_opts):
    net_attrs = {'bonding': 'james',
                 'custom': ethtool_opts,
                 'bootproto': 'dhcp', 'STP': 'no', 'bridged': 'true'}

    for subcommand in _parse_into_subcommands(
            net_attrs['custom']['ethtool_opts'].split(' ')):
        _validate_dev_ownership(nics, 'test_net', subcommand)


def test():
    opts = {'ethtool_opts':
            '--coalesce em1 rx-usecs 14 sample-interval 3 '
            '--offload em2 rx on lro on tso off '
            '--change em1 speed 1000 duplex half'}
    # Test subcmd split
    print(opts['ethtool_opts'])
    print('splits into: ')
    for subcmd in _parse_into_subcommands(opts['ethtool_opts'].split()):
        command = ([ETHTOOL_BINARY.cmd] + [subcmd.name, subcmd.device]
                   + subcmd.flags)
        print('    ', end=' ')
        print(command)

    # Test with the correct nics
    nics = ('em1', 'em2')
    try:
        _test_cmd_with_nics(nics, opts)
    except Exception:
        raise
    else:
        print('ethtool options hook: Correctly accepted input "%s" for fake '
              'nics %s' % (opts['ethtool_opts'], nics))
    # Test with a subset of the nics
    nics = ('em1',)
    try:
        _test_cmd_with_nics(nics, opts)
    except RuntimeError as rex:
        print('ethtool options hook: Correctly rejected input "%s" for fake '
              'nics %s. Exception: %r' % (opts['ethtool_opts'], nics, rex))
    else:
        raise ValueError('ethtool options hook: Incorrectly accepted input %s '
                         'for fake nics %s' % (opts['ethtool_opts'], nics))


def main():
    """Read ethtool_options from the network 'custom' properties and apply them
    to the network's devices."""
    setup_nets_config = hooking.read_json()
    for network, attrs in setup_nets_config['request']['networks'].items():
        if 'remove' in attrs:
            continue
        elif 'custom' in attrs:
            _process_network(network, attrs)


def _process_network(network, attrs):
    """Applies ethtool_options to the network if necessary"""
    options = attrs['custom'].get('ethtool_opts')
    if options is not None:
        nics = _net_nics(attrs)
        for subcmd in _parse_into_subcommands(options.split()):
            if subcmd.device == ALL_SLAVES:
                expanded_nics = nics
            else:
                _validate_dev_ownership(nics, network, subcmd)
                expanded_nics = (subcmd.device,)
            for nic in expanded_nics:
                try:
                    _set_ethtool_opts(network,
                                      [subcmd.name, nic] + subcmd.flags)
                except EthtoolError as ee:
                    hooking.log(str(ee))


def _net_nics(attrs):
    if 'bonding' in attrs:
        return Bond(attrs['bonding']).slaves
    else:
        return [attrs.pop('nic')] if 'nic' in attrs else ()


def _validate_dev_ownership(nics, name, subcommand):
    """Takes ethtool subcommands and raises an exception if there is a device
    that does not belong to the network"""
    if not nics:
        raise RuntimeError('Network %s has no nics.' % name)

    if subcommand.device not in nics:
        raise RuntimeError('Trying to apply ethtool opts for dev: %s, not in '
                           '%s nics: %s' % (subcommand.device, name, nics))


def _parse_into_subcommands(tokens):
    current = []
    for token in tokens:
        if token.startswith('-') and current:
            yield Subcommand(current[0], current[1], current[2:])
            current = []
        current.append(token)
    if current:
        yield Subcommand(current[0], current[1], current[2:])


def _set_ethtool_opts(network, options):
    """Takes an iterable of the tokenized ethtool command line arguments and
    applies them to the network devices"""
    command = [ETHTOOL_BINARY.cmd] + options
    rc, _, err = hooking.execCmd(command)
    if rc != 0:
        raise EthtoolError('Failed to set ethtool opts (%s) for network %s. '
                           'Err: %s' % (' '.join(options), network, err))


if __name__ == '__main__':
    try:
        if '--test' in sys.argv:
            test()
        else:
            main()
    except:
        hooking.exit_hook('ethtool_options hook: [unexpected error]: %s\n' %
                          traceback.format_exc())
