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
from contextlib import contextmanager
import sys

from libvirt import libvirtError

from hooking import execCmd

from vdsm.utils import CommandPath

# TODO: move required modules into vdsm/lib
sys.path.append('/usr/share/vdsm')
from network.configurators import libvirt
import supervdsm

EXT_IP = CommandPath('ip', '/sbin/ip').cmd
EXT_OVS_VSCTL = CommandPath('ovs-vsctl',
                            '/usr/sbin/ovs-vsctl',
                            '/usr/bin/ovs-vsctl').cmd
EXT_OVS_APPCTL = CommandPath('ovs-appctl',
                             '/usr/sbin/ovs-appctl',
                             '/usr/bin/ovs-appctl').cmd
BRIDGE_NAME = 'ovsbr0'


def rget(dict, keys, default=None):
    """ Recursive dictionary.get()
    >>> rget({'a': {'b': 'hello'}}, ('a', 'b'))
    'hello'
    """
    if dict is None:
        return default
    elif len(keys) == 0:
        return dict
    return rget(dict.get(keys[0]), keys[1:], default)


def get_bond_options(options, keep_custom=False):
    """ Parse bonding options into dictionary, if keep_custom is set to True,
    custom option will not be recursive parsed.
    >>> get_bond_options('mode=4 custom=foo=yes,bar=no')
    {'custom': {'bar': 'no', 'foo': 'yes'}, 'mode': '4'}
    """
    def _string_to_dict(str, div):
        if options == '':
            return {}
        return dict(option.split('=', 1)
                    for option in str.strip(div).split(div))
    if options:
        d_options = _string_to_dict(options, ' ')
        if d_options.get('custom') and not keep_custom:
            d_options['custom'] = _string_to_dict(d_options['custom'], ',')
        return d_options
    else:
        return {}


def get_bool(input):
    if input in (1, True, 'True', 'true', 'Yes', 'yes', 'on'):
        return True
    else:
        return False


def is_ovs_network(network_attrs):
    return get_bool(rget(network_attrs, ('custom', 'ovs')))


def is_ovs_bond(bond_attrs):
    bond_options = get_bond_options(bond_attrs.get('options', ''))
    ovs_bond = get_bool(rget(bond_options, ('custom', 'ovs')))
    return ovs_bond


def iter_ovs_nets(networks):
    """ Yields OVS networks (network, attrs) from networks dictionary. """
    for network, attrs in networks.items():
        if is_ovs_network(attrs):
            yield network, attrs


def iter_ovs_bonds(bondings):
    """ Yields OVS bondings (bonding, attrs) from bonds dictionary. """
    for bond, attrs in bondings.items():
        if is_ovs_bond(attrs):
            yield bond, attrs


@contextmanager
def suppress(exception=Exception):
    """ Python 3 suppress context manager.
    https://docs.python.org/3/library/contextlib.html#contextlib.suppress
    """
    try:
        yield
    except exception:
        pass


def destroy_ovs_bridge():
    commands = [EXT_OVS_VSCTL, '--if-exists', 'del-br', BRIDGE_NAME]
    rc, _, err = execCmd(commands)
    if rc != 0:
        raise Exception('\n'.join(err))


def rollback(running_config, initial_config):
    diff = running_config.diffFrom(initial_config)
    if diff:
        for libvirt_ovs_nets in (iter_ovs_nets(running_config.networks),
                                 iter_ovs_nets(initial_config.networks)):
            for net, attrs in libvirt_ovs_nets:
                with suppress(libvirtError):  # network not found
                    libvirt.removeNetwork(net)

        destroy_ovs_bridge()
        for net, attrs in running_config.networks.items():
            if is_ovs_network(attrs):
                running_config.networks.pop(net)
        for bond, attrs in running_config.bonds.items():
            if is_ovs_bond(attrs):
                running_config.bonds.pop(bond)
        running_config.save()

        supervdsm.getProxy().setupNetworks(
            initial_config.networks, initial_config.bonds,
            {'connectivityCheck': False, '_inRollback': True})
