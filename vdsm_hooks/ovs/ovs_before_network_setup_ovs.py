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
from functools import partial

import six

from vdsm.compat import suppress
from vdsm.network import libvirt
from vdsm.network import netswitch
from vdsm.network.netinfo.cache import NetInfo
from vdsm.network.netinfo.bonding import parse_bond_options
from vdsm.utils import rget

import hooking

from ovs_utils import (is_ovs_bond, iter_ovs_nets, iter_ovs_bonds,
                       destroy_ovs_bridge, BRIDGE_NAME, EXT_OVS_VSCTL)
import ovs_utils

log = partial(ovs_utils.log, tag='ovs_before_network_setup_ovs: ')

VALID_MODES = frozenset(['active-backup', 'balance-tcp', 'balance-slb'])
VALID_LACP = frozenset(['active', 'passive', 'off'])


def _remove_redundant_ovs_bridge(running_config):
    """ Remove OVS Bridge if there is no OVS net/bond anymore. """
    for net, attr in iter_ovs_nets(running_config.networks):
        return
    for bond, attr in iter_ovs_bonds(running_config.bonds):
        return
    log('Removing redundant OVS bridge')
    destroy_ovs_bridge()


def _get_nets_by_nic(running_config):
    """ Transform running config into {nic: set(networks)}. """
    nets_by_nic = {}
    for net, attrs in six.iteritems(running_config.networks):
        nic = attrs.get('nic')
        if nic is not None:
            nets_by_nic.setdefault(nic, set()).add(net)
    return nets_by_nic


def _run_commands(commands):
    """ If there are any needed changes in OVS network listed in commands,
    apply them. Otherwise do nothing.
    """
    if commands:
        commands = [EXT_OVS_VSCTL, '--', '--may-exist', 'add-br',
                    BRIDGE_NAME] + commands
        log('Executing commands: %s' % ' '.join(commands))
        rc, _, err = hooking.execCmd(commands)
        if rc != 0:
            raise Exception('Executing commands failed: %s' % '\n'.join(err))


def _setup_ovs_net(net, attrs, running_config, nets_by_nic):
    commands = []
    nic = attrs.get('nic')
    vlan = attrs.get('vlan')

    if vlan is None:
        commands.extend(_set_stp(attrs))
    else:
        commands.extend(['--', 'add-br', net, BRIDGE_NAME, str(vlan)])
    if nic is not None:
        commands.extend(_add_nic_port(net, nic, nets_by_nic))
    commands.extend(_set_aa_mapping(net, attrs, running_config))

    running_config.setNetwork(net, attrs)
    return commands


def _edit_ovs_net(net, attrs, running_config, nets_by_nic):
    commands = []
    nic = attrs.get('nic')
    vlan = attrs.get('vlan')

    if vlan is None:
        commands.extend(['--', '--if-exists', 'del-br', net])
        commands.extend(_set_stp(attrs))
    else:
        running_vlan = running_config.networks[net].get('vlan')
        if running_vlan is None:
            commands.extend(['--', 'add-br', net, BRIDGE_NAME, str(vlan)])
        elif running_vlan != vlan:
            commands.extend(['--', 'set', 'port', net, 'tag=%d' % vlan])
    running_nic = running_config.networks[net].get('nic')
    if running_nic != nic:
        if running_nic is not None:
            commands.extend(_del_nic_port(net, running_nic, nets_by_nic))
        if nic is not None:
            commands.extend(_add_nic_port(net, nic, nets_by_nic))
    commands.extend(_set_aa_mapping(net, attrs, running_config))

    running_config.setNetwork(net, attrs)
    return commands


def _remove_ovs_network(net, running_config, nets_by_nic):
    commands = []
    net_conf = running_config.networks.get(net)
    if 'vlan' in net_conf:
        commands.extend(['--', 'del-br', net])
    if 'nic' in net_conf:
        commands.extend(_del_nic_port(net, net_conf.get('nic'), nets_by_nic))
    running_config.removeNetwork(net)
    with suppress():
        libvirt.removeNetwork(net)
    return commands


def _add_nic_port(net, nic, nets_by_nic):
    if nic in nets_by_nic:
        nets_by_nic[nic].add(net)
    else:
        nets_by_nic[nic] = set([net])
    return ['--', '--may-exist', 'add-port', BRIDGE_NAME, nic]


def _del_nic_port(net, nic, nets_by_nic):
    nets_by_nic[nic].remove(net)
    if len(nets_by_nic[nic]) == 0:
        return ['--', '--if-exists', 'del-port', BRIDGE_NAME, nic]
    else:
        return []


def _set_stp(attrs):
    # Supported only by OVS Bridge
    stp = attrs.get('stp', False)
    return ['--', 'set', 'Bridge', BRIDGE_NAME,
            'stp_enable=%s' % str(stp).lower()]


def _set_aa_mapping(network, attrs, running_config):
    """Handle OVS Auto-Attach mapping. This requires openvswitch >= 2.4"""
    command = []
    init_sid = rget(running_config.networks, (network, 'custom', 'ovs_aa_sid'))
    init_vlan = rget(running_config.networks, (network, 'vlan'))
    sid = rget(attrs, ('custom', 'ovs_aa_sid'))
    vlan = attrs.get('vlan')
    if init_sid != sid or init_vlan != vlan:  # if configuration differs
        if init_sid is not None:
            command.extend(['--', 'del-aa-mapping', network, str(init_sid),
                            str(init_vlan)])
        if sid is not None:
            interfaces = (
                running_config.bonds.get(attrs['bonding'])['nics']
                if 'bonding' in attrs else [attrs['nic']])
            for interface in interfaces:
                # lldp is disabled by default, see ovs-vswitchd.conf.db(5)
                command.extend(['--', 'set', 'Interface', interface,
                                'lldp:enable=true'])
            command.extend(['--', 'add-aa-mapping', network, str(sid),
                            str(vlan)])

    return command


def _get_untagged_net(running_config):
    for network, attrs in iter_ovs_nets(running_config.networks):
        if 'vlan' not in attrs:
            return network
    return None


def _validate_net_configuration(net, attrs, running_config, netinfo):
    nic = attrs.get('nic')
    bonding = attrs.get('bonding')
    vlan = attrs.get('vlan')
    stp = attrs.get('stp', False)

    if bonding in running_config.bonds:
        if not is_ovs_bond(running_config.bonds[bonding]):
            raise Exception('%s is not OVS bonding' % bonding)
    if nic is not None and nic not in netinfo.nics:
        raise Exception('Nic %s does not exist' % nic)
    if vlan is not None and bonding is None and nic is None:
        raise Exception('You can not create a nicless/bondless vlan')

    for existing_net, existing_attrs in six.iteritems(running_config.networks):
        if (existing_net != net and
                existing_attrs.get('nic') == nic and
                existing_attrs.get('bond') == bonding and
                existing_attrs.get('vlan') == vlan):
            raise Exception('%s is already used by network %s' %
                            ((nic or bonding), existing_net))
    if vlan is None:
        untagged_net = _get_untagged_net(running_config)
        if untagged_net not in (None, net):
            raise Exception('Untagged network already defined with name %s' %
                            untagged_net)
        if rget(attrs, ('custom', 'ovs_aa_sid')) is not None:
            raise Exception('Cannot define aa-mapping on untagged network')
    if stp and vlan is not None:
        raise Exception('STP could be set only on untagged networks')


def _setup_ovs_bond(bond, attrs, running_config):
    """ Add OVS bonding and set it requested mode and lacp options.
    As we use custom entry, these values are not validated in network api,
    so we check correct values here.
    """
    commands = []
    commands.extend(['--', '--fake-iface', '--may-exist', 'add-bond',
                     BRIDGE_NAME, bond] + attrs.get('nics'))

    bond_options = parse_bond_options(attrs.get('options'))
    mode = rget(bond_options, ('custom', 'ovs_mode')) or 'active-backup'
    lacp = rget(bond_options, ('custom', 'ovs_lacp')) or 'off'
    commands.extend(['--', 'set', 'port', bond, 'bond_mode=%s' % mode])
    commands.extend(['--', 'set', 'port', bond, 'lacp=%s' % lacp])

    running_config.setBonding(bond, {'nics': attrs.get('nics'),
                                     'options': attrs.get('options')})
    return commands


def _edit_ovs_bond(bond, attrs, running_config):
    """ We have to use database commands to change slaves of running
    bonding, then we continue with standard bond setup.
    """
    commands = []
    current = set(rget(running_config.bonds, (bond, 'nics')))
    new = set(attrs.get('nics'))
    add = new - current
    remove = current - new
    for nic in add:
        commands.extend(['--', '--id=@' + nic, 'create', 'Interface', 'name=' +
                         nic, '--', 'add', 'Port', bond, 'interfaces', '@' +
                         nic])
    for nic in remove:
        commands.extend(['--', '--id=@' + nic, 'get', 'Interface', nic, '--',
                         'remove', 'Port', bond, 'interfaces', '@' + nic])

    commands.extend(_setup_ovs_bond(bond, attrs, running_config))
    return commands


def _validate_bond_configuration(attrs, netinfo):
    nics = attrs.get('nics')
    bond_options = parse_bond_options(attrs.get('options'))

    if nics is None or len(attrs.get('nics')) < 2:
        raise Exception('You have to define at least 2 slaves for '
                        'OVS bonding')
    for nic in nics:
        if nic not in netinfo.nics:
            raise Exception('Nic %s does not exist' % nic)

    mode = rget(bond_options, ('custom', 'ovs_mode')) or 'active-backup'
    lacp = rget(bond_options, ('custom', 'ovs_lacp')) or 'off'
    if mode:
        if mode not in VALID_MODES:
            raise Exception('%s is not valid ovs bond mode' % mode)
    if lacp:
        if lacp not in VALID_LACP:
            raise Exception('%s is not valid ovs lacp value' % lacp)


def _handle_setup(nets, bonds, running_config, nets_by_nic):
    commands = []
    netinfo = NetInfo(netswitch.netinfo())
    for bond, attrs in six.iteritems(bonds):
        if 'remove' not in attrs:
            _validate_bond_configuration(attrs, netinfo)
            if bond in running_config.bonds:
                commands.extend(_edit_ovs_bond(bond, attrs, running_config))
            else:
                commands.extend(_setup_ovs_bond(bond, attrs, running_config))
    for net, attrs in six.iteritems(nets):
        if 'remove' not in attrs:
            _validate_net_configuration(net, attrs, running_config, netinfo)
            if net in running_config.networks:
                commands.extend(_edit_ovs_net(net, attrs, running_config,
                                              nets_by_nic))
            else:
                commands.extend(_setup_ovs_net(net, attrs, running_config,
                                               nets_by_nic))
    return commands


def _handle_removal(nets, bonds, running_config, nets_by_nic):
    commands = []
    for net, attrs in six.iteritems(nets):
        if 'remove' in attrs:
            commands.extend(_remove_ovs_network(net, running_config,
                                                nets_by_nic))
    for bond, attrs in six.iteritems(bonds):
        if 'remove' in attrs:
            commands.extend(['--', 'del-port', BRIDGE_NAME, bond])
            running_config.removeBonding(bond)
    return commands


def prepare_ovs(nets, bonds, running_config):
    nets_by_nic = _get_nets_by_nic(running_config)
    commands = []
    commands.extend(_handle_removal(nets, bonds, running_config, nets_by_nic))
    commands.extend(_handle_setup(nets, bonds, running_config, nets_by_nic))
    return commands


def configure_ovs(commands, running_config):
    _run_commands(commands)
    _remove_redundant_ovs_bridge(running_config)
