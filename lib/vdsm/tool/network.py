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
from __future__ import print_function

import argparse
import logging
import logging.config
import threading

import six

from vdsm.network import api as net_api
from vdsm.network import netupgrade
from vdsm.network.link.bond import sysfs_options_mapper
from vdsm.network.restore_net_config import restore

from . import expose, ExtraArgsError


@expose('upgrade-networks')
def upgrade_networks(*args):
    """
    upgrade-networks

    Upgrade networks configuration to up-to-date format.
    """
    netupgrade.upgrade()


@expose('restore-nets')
def restore_command(*args):
    """
    restore-nets
    Restores the networks to what was previously persisted via vdsm.
    """
    threading.current_thread().setName('restore-net')
    try:
        logging.config.fileConfig('/etc/vdsm/svdsm.logger.conf',
                                  disable_existing_loggers=False)
    except:
        logging.basicConfig(filename='/dev/stderr', filemode='w+',
                            level=logging.DEBUG)
        logging.error('Could not init proper logging', exc_info=True)

    if len(args) > 2:
        raise ExtraArgsError()

    force_restore = '--force' in args
    restore(force_restore)


@expose('dump-bonding-options')
def dump_bonding_options(*args):
    """dump-bonding-options

    Two actions are taken:
    - Read bonding option defaults (per mode) and dump them to
      BONDING_DEFAULTS in JSON format.
    - Read bonding option possible values (per mode) and dump them to
      BONDING_NAME2NUMERIC_PATH in JSON format.
    """

    if len(args) > 1:
        raise ExtraArgsError()

    sysfs_options_mapper.dump_bonding_options()


@expose('list-nets')
def list_networks(*args):
    """
    list-nets

    List configured VDSM networks and mark the one with default route.
    """
    caps = net_api.network_caps()
    output = ''
    for net, attrs in six.viewitems(caps['networks']):
        output += net
        if attrs['ipv4defaultroute']:
            output += ' (default route)\n'
        else:
            output += '\n'
    print(output, end='')


@expose('clear-nets')
def clear_networks(*args):
    """
    clear-nets [--exclude-net [network to keep [...]]] [--all]

    Remove networks configured by VDSM. Networks that should be kept could
    be listed with --exclude-net argument. In case no network is given,
    explicit --all is required to prevent accidental loss of connectivity.

    This command can be executed before VDSM removal to keep the host clean.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '-e',
        '--exclude-net',
        metavar='EXCLUDED_NETWORK',
        nargs='*',
        default=[],
        help='VDSM networks that should be kept'
    )
    parser.add_argument(
        '-a',
        '--all',
        action='store_true',
        help='set this flag in case no network should be kept'
    )
    arguments = parser.parse_args(args[1:])

    if not arguments.exclude_net and not arguments.all:
        parser.error('Either --exclude-net with a network to be kept or '
                     '--all is required as an argument. Use vdsm-tool '
                     'list-nets to list configured networks.')

    caps = net_api.network_caps()
    networks_request = {
        net: {'remove': True}
        for net in caps['networks']
        if net not in arguments.exclude_net
    }
    net_api.setupNetworks(networks_request, {}, {'connectivityCheck': False})
    net_api.setSafeNetworkConfig()
