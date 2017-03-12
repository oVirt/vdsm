# Copyright 2011-2016 Red Hat, Inc.
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
from __future__ import print_function
from contextlib import contextmanager

import sys
import logging
import six

from vdsm import hooks
from vdsm.network import libvirt
from vdsm.network import sourceroute
from vdsm.network.ipwrapper import DUMMY_BRIDGE
from vdsm.network.link import iface as link_iface
from vdsm.network.link import sriov

from . ip import address as ipaddress, validator as ipvalidator
from . canonicalize import canonicalize_networks, canonicalize_bondings
from . errors import RollbackIncomplete
from . import netconfpersistence
from . import netswitch


DUMMY_BRIDGE


def network_caps():
    """Obtain root-requiring network capabilties

    TODO: When we split netinfo, we will merge root and non-root netinfo in
          caps to reduce the amount of work in root context.
    """
    # TODO: Version requests by engine to ease handling of compatibility.
    return netswitch.netinfo(compatibility=30600)


def change_numvfs(pci_path, numvfs, net_name):
    """Change number of virtual functions of a device.

    The persistence is stored in the same place as other network persistence is
    stored. A call to setSafeNetworkConfig() will persist it across reboots.
    """
    # TODO: net_name is here only because it is hard to call pf_to_net_name
    # TODO: from here. once all our code will be under lib/vdsm this should be
    # TODO: removed.
    logging.info('Changing number of vfs on device %s -> %s.',
                 pci_path, numvfs)
    sriov.update_numvfs(pci_path, numvfs)
    sriov.persist_numvfs(pci_path, numvfs)

    link_iface.up(net_name)


def ip_addrs_info(device):
    """"
    Report IP addresses of a device.

    Returning a 4 values: ipv4addr, ipv4netmask, ipv4addrs, ipv6addrs
    ipv4addrs and ipv6addrs contain (each) a list of addresses.
    ipv4netmask and ipv4addrs represents the 'primary' ipv4 address of the
    device, if it exists.
    """
    return ipaddress.addrs_info(device)


def libvirt_networks():
    """Report libvirt known networks"""
    return libvirt.networks()


def net2vlan(network_name):
    """Return the vlan id of the network if exists, None otherwise."""
    return netswitch.net2vlan(network_name)


def netname_o2l(ovirt_name):
    """Translate ovirt network name to the name used by libvirt database"""
    return libvirt.LIBVIRT_NET_PREFIX + ovirt_name


def netname_l2o(libvirt_name):
    """Translate the name used by libvirt database to the ovirt network name"""
    return libvirt_name[len(libvirt.LIBVIRT_NET_PREFIX):]


def ovs_bridge(network_name):
    """
    If network_name is an OVS based network, return the OVS (real) bridge.
    Otherwise, return None.

    This API requires root access.
    """
    return netswitch.ovs_net2bridge(network_name)


def _build_setup_hook_dict(req_networks, req_bondings, req_options):

    hook_dict = {'request': {'networks': dict(req_networks),
                             'bondings': dict(req_bondings),
                             'options': dict(req_options)}}

    return hook_dict


def _apply_hook(bondings, networks, options):
    results = hooks.before_network_setup(
        _build_setup_hook_dict(networks, bondings, options))
    # gather any changes that could have been done by the hook scripts
    networks = results['request']['networks']
    bondings = results['request']['bondings']
    options = results['request']['options']
    return bondings, networks, options


@contextmanager
def _rollback():
    try:
        yield
    except RollbackIncomplete as roi:
        diff, excType, value = roi
        tb = sys.exc_info()[2]
        try:
            # diff holds the difference between RunningConfig on disk and
            # the one in memory with the addition of {'remove': True}
            # hence, the next call to setupNetworks will perform a cleanup.
            setupNetworks(diff.networks, diff.bonds,
                          {'inRollback': True, 'connectivityCheck': 0})
        except Exception:
            logging.error('Memory rollback failed.', exc_info=True)
        finally:
            # We raise the original unexpected exception since any
            # exception that might have happened on rollback is
            # properly logged and derived from actions to respond to
            # the original exception.
            six.reraise(excType, value, tb)


def setupNetworks(networks, bondings, options):
    """Add/Edit/Remove configuration for networks and bondings.

    Params:
        networks - dict of key=network, value=attributes
            where 'attributes' is a dict with the following optional items:
                        vlan=<id>
                        bonding="<name>" | nic="<name>"
                        (bonding and nics are mutually exclusive)
                        ipaddr="<ipv4>"
                        netmask="<ipv4>" | prefix=<prefixlen>
                        gateway="<ipv4>"
                        bootproto="..."
                        ipv6addr="<ipv6>[/<prefixlen>]"
                        ipv6gateway="<ipv6>"
                        ipv6autoconf="0|1"
                        dhcpv6="0|1"
                        defaultRoute=True|False
                        nameservers=[<dns1>, <dns2> ...]"
                        switch="legacy|..."
                        (other options will be passed to the config file AS-IS)
                        -- OR --
                        remove=True (other attributes can't be specified)

        bondings - dict of key=bonding, value=attributes
            where 'attributes' is a dict with the following optional items:
                        nics=["<nic1>" , "<nic2>", ...]
                        options="<bonding-options>"
                        switch="legacy|..."
                        -- OR --
                        remove=True (other attributes can't be specified)

        options - dict of options, such as:
                        connectivityCheck=0|1
                        connectivityTimeout=<int>
                        _inRollback=True|False

    Notes:
        When you edit a network that is attached to a bonding, it's not
        necessary to re-specify the bonding (you need only to note
        the attachment in the network's attributes). Similarly, if you edit
        a bonding, it's not necessary to specify its networks.
    """
    logging.debug('Setting up network according to configuration: '
                  'networks:%r, bondings:%r, options:%r' % (networks,
                                                            bondings, options))
    try:
        canonicalize_networks(networks)
        canonicalize_bondings(bondings)

        logging.debug('Validating configuration')
        ipvalidator.validate(networks)
        netswitch.validate(networks, bondings)

        running_config = netconfpersistence.RunningConfig()
        if netswitch.switch_type_change_needed(
                networks, bondings, running_config):
            _change_switch_type(networks, bondings, options, running_config)
        else:
            _setup_networks(networks, bondings, options)
    except:
        # TODO: it might be useful to pass failure description in 'response'
        # field
        network_config_dict = {
            'request': {'networks': dict(networks),
                        'bondings': dict(bondings),
                        'options': dict(options)}}
        hooks.after_network_setup_fail(network_config_dict)
        raise
    else:
        hooks.after_network_setup(
            _build_setup_hook_dict(networks, bondings, options))


def _setup_networks(networks, bondings, options):
    bondings, networks, options = _apply_hook(bondings, networks, options)

    logging.debug('Applying...')
    in_rollback = options.get('_inRollback', False)
    with _rollback():
        netswitch.setup(networks, bondings, options, in_rollback)


def _change_switch_type(networks, bondings, options, running_config):
    logging.debug('Applying switch type change')

    netswitch.validate_switch_type_change(networks, bondings, running_config)

    in_rollback = options.get('_inRollback', False)

    logging.debug('Removing current switch configuration')
    with _rollback():
        _remove_nets_and_bonds(networks, bondings, in_rollback)

    logging.debug('Setting up requested switch configuration')
    try:
        with _rollback():
            netswitch.setup(networks, bondings, options, in_rollback)
    except:
        logging.exception('Requested switch setup failed, rolling back to '
                          'initial configuration')
        diff = running_config.diffFrom(netconfpersistence.RunningConfig())
        try:
            netswitch.setup(
                diff.networks, diff.bonds, {'connectivityCheck': False},
                in_rollback=True)
        except:
            logging.exception('Failed during rollback')
            raise
        raise


def _remove_nets_and_bonds(nets, bonds, in_rollback):
    nets_removal = {name: {'remove': True} for name in six.iterkeys(nets)}
    bonds_removal = {name: {'remove': True} for name in six.iterkeys(bonds)}
    netswitch.setup(
        nets_removal, bonds_removal, {'connectivityCheck': False}, in_rollback)


def setSafeNetworkConfig():
    """Declare current network configuration as 'safe'"""
    netconfpersistence.RunningConfig.store()


def add_sourceroute(iface, ip, mask, route):
    sourceroute.add(iface, ip, mask, route)


def remove_sourceroute(iface):
    sourceroute.remove(iface)
