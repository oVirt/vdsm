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
from copy import deepcopy
import sys
import traceback

from vdsm.netconfpersistence import RunningConfig

from hooking import execCmd
import hooking

from ovs_utils import (is_ovs_network, is_ovs_bond, rollback, EXT_IP,
                       EXT_OVS_VSCTL)
from ovs_setup_ovs import configure_ovs, prepare_ovs
from ovs_setup_ip import configure_ip
from ovs_setup_mtu import configure_mtu
from ovs_setup_libvirt import (create_libvirt_nets, remove_libvirt_nets,
                               prepare_libvirt)


def _separate_ovs_nets_bonds(nets, bonds, running_config):
    """ Get a dictionaries of nets and bonds to be handled by OVS hook and
    those to be handled by standard configurator.
    """
    ovs_nets = {}
    non_ovs_nets = {}
    ovs_bonds = {}
    non_ovs_bonds = {}
    for net, attrs in nets.items():
        if (('remove' in attrs and net in running_config.networks and
                is_ovs_network(running_config.networks[net])) or
                is_ovs_network(attrs)):
            ovs_nets[net] = attrs
        else:
            non_ovs_nets[net] = attrs
    for bond, attrs in bonds.items():
        if (('remove' in attrs and bond in running_config.bonds and
                is_ovs_bond(running_config.bonds[bond])) or
                is_ovs_bond(attrs)):
            ovs_bonds[bond] = attrs
        else:
            non_ovs_bonds[bond] = attrs
    return ovs_nets, non_ovs_nets, ovs_bonds, non_ovs_bonds


@contextmanager
def _rollback(running_config, initial_config, in_rollback):
    try:
        yield
    except:
        if in_rollback:
            hooking.log('Failed while trying to rollback:')
        else:
            hooking.log('Configuration failed. Entering rollback.')
            rollback(running_config, initial_config)
            hooking.log('Rollback finished. Initial error:')
        raise


def configure(nets, bonds, running_config, in_rollback):
    initial_config = deepcopy(running_config)

    commands = prepare_ovs(nets, bonds, running_config)
    libvirt_create, libvirt_remove = prepare_libvirt(nets, running_config)
    with _rollback(running_config, initial_config, in_rollback):
        remove_libvirt_nets(libvirt_remove)
        configure_ovs(commands, running_config)
        configure_mtu(running_config)
        configure_ip(nets, initial_config.networks)
        create_libvirt_nets(libvirt_create)

    hooking.log('Saving running configuration: %s %s' %
                (running_config.networks, running_config.bonds))
    running_config.save()


def main():
    setup_nets_config = hooking.read_json()
    hooking.log('Hook started, handling: %s' % setup_nets_config)

    running_config = RunningConfig()
    networks = setup_nets_config['request']['networks']
    bondings = setup_nets_config['request']['bondings']
    inRollback = setup_nets_config['request']['options'].get('_inRollback',
                                                             False)
    ovs_nets, non_ovs_nets, ovs_bonds, non_ovs_bonds = \
        _separate_ovs_nets_bonds(networks, bondings, running_config)
    configure(ovs_nets, ovs_bonds, running_config, inRollback)

    setup_nets_config['request']['bondings'] = non_ovs_bonds
    setup_nets_config['request']['networks'] = non_ovs_nets
    hooking.log('Hook finished, returning non-OVS networks and bondings back '
                'to VDSM: %s' % setup_nets_config)
    hooking.write_json(setup_nets_config)


def _execCmd(cmd, exit=True):
    print('> ' + ' '.join(cmd))
    rc, out, err = execCmd(cmd)
    if rc == 0:
        for l in out:
            print(l)
    else:
        print('error %d' % rc)
        for l in err:
            print(err)
        if exit:
            raise RuntimeError()


def test_add():
    import hooks
    _execCmd([EXT_OVS_VSCTL, 'show'])
    json_input = {
        'request': {
            'networks': {
                'ovs-test-net': {'bonding': 'bond1515', 'bridged': True,
                                 'vlan': 122, 'custom': {'ovs': True}}},
            'bondings': {
                'bond1515': {'nics': ['dummy_1', 'dummy_2'],
                             'custom': {'ovs': True,
                                        'ovs_bond_mode': 'active-backup'}}}}}
    _execCmd([EXT_IP, 'link', 'add', 'dummy_1', 'type', 'dummy'])
    _execCmd([EXT_IP, 'link', 'add', 'dummy_2', 'type', 'dummy'])
    print("> executing hook with fake json input: " + str(json_input))
    hooks.before_network_setup(json_input)
    print("hook finished")
    _execCmd([EXT_OVS_VSCTL, 'show'])
    _execCmd(['cat', '/var/run/vdsm/netconf/nets/ovs-test-net'])
    _execCmd(['cat', '/var/run/vdsm/netconf/bonds/bond1515'])


def test_del():
    import hooks
    _execCmd([EXT_OVS_VSCTL, 'show'])
    json_input = {'request': {'networks': {'ovs-test-net': {'remove': True}},
                              'bondings': {'bond1515': {'remove': True}}}}
    print("\nexecuting hook with fake json input:")
    print(json_input)
    hooks.before_network_setup(json_input)
    print("hook finished\n")
    _execCmd([EXT_OVS_VSCTL, 'show'])
    _execCmd(['cat', '/var/run/vdsm/netconf/nets/ovs-test-net'], exit=False)
    _execCmd(['cat', '/var/run/vdsm/netconf/bonds/bond1515'], exit=False)
    _execCmd([EXT_IP, 'link', 'del', 'dummy_1'])
    _execCmd([EXT_IP, 'link', 'del', 'dummy_2'])


def test_clean():
    _execCmd([EXT_OVS_VSCTL, 'del-br', 'ovs-test-net'], exit=False)
    _execCmd([EXT_IP, 'link', 'del', 'dummy_1'], exit=False)
    _execCmd([EXT_IP, 'link', 'del', 'dummy_2'], exit=False)


if __name__ == '__main__':
    try:
        if '--test' in sys.argv:
            sys.path.extend(['../../vdsm', '../../lib'])
            if 'add' in sys.argv:
                test_add()
            elif 'del' in sys.argv:
                test_del()
            elif 'clean' in sys.argv:
                test_clean()
        else:
            main()
    except:
        hooking.exit_hook(traceback.format_exc())
