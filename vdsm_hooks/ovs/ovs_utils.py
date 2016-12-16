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
import six

from hooking import execCmd
import hooking

from vdsm.network.netinfo.bonding import parse_bond_options
from vdsm.utils import CommandPath, rget

EXT_IP = CommandPath('ip', '/sbin/ip').cmd
EXT_OVS_VSCTL = CommandPath('ovs-vsctl',
                            '/usr/sbin/ovs-vsctl',
                            '/usr/bin/ovs-vsctl').cmd
EXT_OVS_APPCTL = CommandPath('ovs-appctl',
                             '/usr/sbin/ovs-appctl',
                             '/usr/bin/ovs-appctl').cmd
BRIDGE_NAME = 'ovsbr0'

INIT_CONFIG_FILE = '/tmp/ovs_init_config'  # TODO: VDSM tmp folder


def get_bool(input):
    if input in (1, True, 'True', 'true', 'Yes', 'yes', 'on'):
        return True
    else:
        return False


def is_ovs_network(network_attrs):
    return get_bool(rget(network_attrs, ('custom', 'ovs')))


def is_ovs_bond(bond_attrs):
    bond_options = parse_bond_options(bond_attrs.get('options', ''))
    ovs_bond = get_bool(rget(bond_options, ('custom', 'ovs')))
    return ovs_bond


def iter_ovs_nets(networks):
    """ Yields OVS networks (network, attrs) from networks dictionary. """
    for network, attrs in six.iteritems(networks):
        if is_ovs_network(attrs):
            yield network, attrs


def iter_ovs_bonds(bondings):
    """ Yields OVS bondings (bonding, attrs) from bonds dictionary. """
    for bond, attrs in six.iteritems(bondings):
        if is_ovs_bond(attrs):
            yield bond, attrs


def destroy_ovs_bridge():
    commands = [EXT_OVS_VSCTL, '--if-exists', 'del-br', BRIDGE_NAME]
    rc, _, err = execCmd(commands)
    if rc != 0:
        raise Exception('\n'.join(err))


def log(message, tag='OVS: '):
    hooking.log('%s%s' % (tag, message))


def ovs_bridge_exists(bridge):
    commands = [EXT_OVS_VSCTL, 'br-exists', bridge]
    rc, _, err = execCmd(commands)
    if rc == 0:
        return True
    elif rc == 2:
        return False
    else:
        raise Exception('\n'.join(err))
