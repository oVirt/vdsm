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
try:
    import cPickle as pickle
except ImportError:
    # Python 3 uses cPickle by default
    import pickle

from copy import deepcopy
from functools import partial
import errno
import sys
import traceback

from libvirt import libvirtError
import six

from vdsm import hooks
from vdsm.compat import suppress
from vdsm.network import libvirt
from vdsm.network.ipwrapper import linkSet
from vdsm.network.netconfpersistence import RunningConfig

from hooking import execCmd
import hooking

from ovs_utils import (is_ovs_network, is_ovs_bond, iter_ovs_nets,
                       destroy_ovs_bridge, ovs_bridge_exists, EXT_IP,
                       EXT_OVS_VSCTL, BRIDGE_NAME, INIT_CONFIG_FILE)
from ovs_setup_ovs import configure_ovs, prepare_ovs
from ovs_setup_ip import configure_ip
from ovs_setup_mtu import configure_mtu
from ovs_setup_libvirt import (create_libvirt_nets, remove_libvirt_nets,
                               prepare_libvirt)
import ovs_utils

log = partial(ovs_utils.log, tag='ovs_before_network_setup: ')


def _set_nets_bonds(config, nets, bonds):
    config['networks'] = nets
    config['bondings'] = bonds


def _separate_ovs_nets_bonds(nets, bonds, running_config):
    """ Get a dictionaries of nets and bonds to be handled by OVS hook and
    those to be handled by standard configurator.
    """
    ovs_nets = {}
    non_ovs_nets = {}
    ovs_bonds = {}
    non_ovs_bonds = {}
    for net, attrs in six.iteritems(nets):
        if (('remove' in attrs and net in running_config.networks and
                is_ovs_network(running_config.networks[net])) or
                is_ovs_network(attrs)):
            ovs_nets[net] = attrs
        else:
            non_ovs_nets[net] = attrs
    for bond, attrs in six.iteritems(bonds):
        if (('remove' in attrs and bond in running_config.bonds and
                is_ovs_bond(running_config.bonds[bond])) or
                is_ovs_bond(attrs)):
            ovs_bonds[bond] = attrs
        else:
            non_ovs_bonds[bond] = attrs
    return ovs_nets, non_ovs_nets, ovs_bonds, non_ovs_bonds


def _destroy_ovs_libvirt_nets(initial_config, running_config):
    log('Removing OVS and libvirt networks: %s %s' % (initial_config,
                                                      running_config))
    for libvirt_ovs_nets in (iter_ovs_nets(running_config.networks),
                             iter_ovs_nets(initial_config.networks)):
        for net, attrs in libvirt_ovs_nets:
            with suppress(libvirtError):  # network not found
                libvirt.removeNetwork(net)

    destroy_ovs_bridge()


def _drop_ovs_nets_config(running_config):
    for net, attrs in list(six.iteritems(running_config.networks)):
        if is_ovs_network(attrs):
            running_config.networks.pop(net)
    for bond, attrs in list(six.iteritems(running_config.bonds)):
        if is_ovs_bond(attrs):
            running_config.bonds.pop(bond)
    running_config.save()


def _load_init_config():
    try:
        with open(INIT_CONFIG_FILE) as f:
            init_config = pickle.load(f)
    except IOError as e:
        if e.errno != errno.ENOENT:
            raise
        return None
    else:
        return init_config


def _save_init_config(init_config):
    with open(INIT_CONFIG_FILE, 'w') as f:
        pickle.dump(init_config, f)


def _rollback(running_config):
    initial_config = _load_init_config()
    if initial_config is None:
        log('No needed OVS changes to be done.')
    else:
        log('Removing OVS networks.')
        _destroy_ovs_libvirt_nets(initial_config, running_config)
        _drop_ovs_nets_config(running_config)
        log('Reconfiguring OVS networks according to initial_config.')
        _configure(initial_config.networks, initial_config.bonds,
                   running_config, save_init_config=False)


def _set_devices_up(nets, bonds):
    devices = set()
    for net, attrs in six.iteritems(nets):
        if 'remove' not in attrs:
            if 'vlan' in attrs:
                devices.add(net)
            if 'nic' in attrs or 'bond' in attrs:
                devices.add(attrs.get('nic') or attrs.get('bond'))
    for bond, attrs in six.iteritems(bonds):
        if 'remove' not in attrs:
            devices.add(bond)
            devices.update(attrs['nics'])
    if ovs_bridge_exists(BRIDGE_NAME):
        devices.add(BRIDGE_NAME)
    for device in devices:
        linkSet(device, ['up'])


def _configure(nets, bonds, running_config, save_init_config=True):
    initial_config = deepcopy(running_config)

    commands = prepare_ovs(nets, bonds, running_config)
    libvirt_create, libvirt_remove = prepare_libvirt(nets, running_config)

    if save_init_config:
        log('Saving initial configuration for optional rollback: %s' %
            initial_config)
        _save_init_config(initial_config)

    remove_libvirt_nets(libvirt_remove)
    configure_ovs(commands, running_config)
    configure_mtu(running_config)
    configure_ip(nets, initial_config.networks, bonds, initial_config.bonds)
    _set_devices_up(nets, bonds)

    log('Saving running configuration: %s %s' % (running_config.networks,
                                                 running_config.bonds))
    running_config.save()

    # we have to create libvirt nets last. when an exception occurs, rollback
    # will find created libvirt networks in running config and will be able to
    # remove them
    create_libvirt_nets(libvirt_create)


def main():
    setup_nets_config = hooking.read_json()
    log('Hook started, handling: %s' % setup_nets_config)

    running_config = RunningConfig()
    networks = setup_nets_config['request']['networks']
    bondings = setup_nets_config['request']['bondings']

    in_ovs_rollback = setup_nets_config['request']['options'].get(
        '_inOVSRollback')

    if in_ovs_rollback:
        log('OVS rollback is to be done.')
        _rollback(running_config)
        _set_nets_bonds(setup_nets_config['request'], {}, {})
        log('OVS rollback finished, returning empty networks and bondings '
            'configuration back to VDSM.')
    else:
        ovs_nets, non_ovs_nets, ovs_bonds, non_ovs_bonds = \
            _separate_ovs_nets_bonds(networks, bondings, running_config)
        if ovs_nets or ovs_bonds:
            _configure(ovs_nets, ovs_bonds, running_config)
            _set_nets_bonds(setup_nets_config['request'], non_ovs_nets,
                            non_ovs_bonds)
        log('Hook finished, returning non-OVS networks and bondings back to '
            'VDSM: %s' % setup_nets_config)

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
