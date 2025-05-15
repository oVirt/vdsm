# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from contextlib import contextmanager

import sys
import logging
import copy

from vdsm.common import hooks

from vdsm.network import connectivity
from vdsm.network import netstats
from vdsm.network import netswitch
from vdsm.network import ovn
from vdsm.network import validator
from vdsm.network.dhcp_monitor import MonitoredItemPool
from vdsm.network.ipwrapper import DUMMY_BRIDGE
from vdsm.network.link import sriov
from vdsm.network.lldp import info as lldp_info
from vdsm.network.nmstate import (
    add_dynamic_source_route_rules as nmstate_add_dynamic_source_route_rules,
)
from vdsm.network.nmstate import update_num_vfs

from . import canonicalize
from .errors import RollbackIncomplete
from . import netconfpersistence


DUMMY_BRIDGE


def network_caps():
    """Obtain root-requiring network capabilties

    TODO: When we split netinfo, we will merge root and non-root netinfo in
          caps to reduce the amount of work in root context.
    """
    # TODO: Version requests by engine to ease handling of compatibility.
    return netswitch.configurator.netcaps(compatibility=30600)


def network_stats():
    """Report network statistics"""
    return netstats.report()


def change_numvfs(numvfs, devname):
    """Change number of virtual functions of a device.

    The persistence is stored in the same place as other network persistence is
    stored. A call to setSafeNetworkConfig() will persist it across reboots.
    """
    logging.info('Changing number of vfs on device %s -> %s.', devname, numvfs)
    update_num_vfs(devname, numvfs)
    sriov.persist_numvfs(devname, numvfs)


def network_northbound(network_name):
    """
    Return the northbound iface of a given network if exists, None otherwise.
    On a legacy network, the NB is either the bridge or the NIC/Bond/VLAN.
    On an OVS network, the NB is a dedicated internal iface connected to the
    OVS switch, named as the network.
    """
    return netswitch.configurator.net2northbound(network_name)


def _build_setup_hook_dict(req_networks, req_bondings, req_options):

    hook_dict = {
        'request': {
            'networks': dict(req_networks),
            'bondings': dict(req_bondings),
            'options': dict(req_options),
        }
    }

    return hook_dict


def _apply_hook(bondings, networks, options):
    results = hooks.before_network_setup(
        _build_setup_hook_dict(networks, bondings, options)
    )
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
        tb = sys.exc_info()[2]
        try:
            # diff holds the difference between RunningConfig on disk and
            # the one in memory with the addition of {'remove': True}
            # hence, the next call to setupNetworks will perform a cleanup.
            setupNetworks(
                roi.diff.networks,
                roi.diff.bonds,
                {'_inRollback': True, 'connectivityCheck': 0},
            )
        except Exception:
            logging.error('Memory rollback failed.', exc_info=True)
        finally:
            # We raise the original unexpected exception since any
            # exception that might have happened on rollback is
            # properly logged and derived from actions to respond to
            # the original exception.
            raise roi.value.with_traceback(tb)


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
                        hwaddr="<mac address>"
                        switch="legacy|..."
                        -- OR --
                        remove=True (other attributes can't be specified)

        options - dict of options, such as:
                        connectivityCheck=0|1
                        connectivityTimeout=<int>
                        _inRollback=True|False
                        commitOnSuccess=True|False

    Notes:
        When you edit a network that is attached to a bonding, it's not
        necessary to re-specify the bonding (you need only to note
        the attachment in the network's attributes). Similarly, if you edit
        a bonding, it's not necessary to specify its networks.
    """
    networks = copy.deepcopy(networks)
    bondings = copy.deepcopy(bondings)
    options = copy.deepcopy(options)

    logging.info(
        'Setting up network according to configuration: '
        'networks:%r, bondings:%r, options:%r' % (networks, bondings, options)
    )
    try:
        canonicalize.canonicalize_networks(networks)
        canonicalize.canonicalize_external_bonds_used_by_nets(
            networks, bondings
        )
        canonicalize.canonicalize_bondings(bondings)

        net_info = netswitch.configurator.netinfo()
        running_config = netconfpersistence.RunningConfig()

        validator.validate(networks, bondings, net_info, running_config)

        if netswitch.configurator.switch_type_change_needed(
            networks, bondings, running_config
        ):
            _change_switch_type(networks, bondings, options, running_config)
        else:
            _setup_networks(networks, bondings, options)
    except:
        # TODO: it might be useful to pass failure description in 'response'
        # field
        network_config_dict = {
            'request': {
                'networks': dict(networks),
                'bondings': dict(bondings),
                'options': dict(options),
            }
        }
        hooks.after_network_setup_fail(network_config_dict)
        raise
    else:
        hooks.after_network_setup(
            _build_setup_hook_dict(networks, bondings, options)
        )


def _setup_networks(networks, bondings, options):
    bondings, networks, options = _apply_hook(bondings, networks, options)

    in_rollback = options.get('_inRollback', False)
    with _rollback():
        netswitch.configurator.setup(networks, bondings, options, in_rollback)


def _change_switch_type(networks, bondings, options, running_config):
    logging.debug('Applying switch type change')

    netswitch.configurator.validate_switch_type_change(
        networks, bondings, running_config
    )

    in_rollback = options.get('_inRollback', False)

    logging.debug('Removing current switch configuration')
    with _rollback():
        _remove_nets_and_bonds(networks, bondings, in_rollback)

    logging.debug('Setting up requested switch configuration')
    try:
        with _rollback():
            netswitch.configurator.setup(
                networks, bondings, options, in_rollback
            )
    except:
        logging.exception(
            'Requested switch setup failed, rolling back to '
            'initial configuration'
        )
        diff = running_config.diffFrom(netconfpersistence.RunningConfig())
        try:
            netswitch.configurator.setup(
                diff.networks,
                diff.bonds,
                {'connectivityCheck': False},
                in_rollback=True,
            )
        except:
            logging.exception('Failed during rollback')
            raise
        raise


def _remove_nets_and_bonds(nets, bonds, in_rollback):
    nets_removal = {name: {'remove': True} for name in nets.keys()}
    bonds_removal = {name: {'remove': True} for name in bonds.keys()}
    netswitch.configurator.setup(
        nets_removal, bonds_removal, {'connectivityCheck': False}, in_rollback
    )


def setSafeNetworkConfig():
    """Declare current network configuration as 'safe'"""
    netswitch.configurator.persist()


def confirm_connectivity():
    connectivity.confirm()


def get_lldp_info(filter):
    """
    If filter is empty, all NICs are returned. If key 'devices' in filter
    contains a list of devices, the list is restricted to this devices.
    An empty list is interpreted as no restriction.
    """
    if not filter.get('devices', []):
        # TODO OVS nics
        filter['devices'] = netswitch.configurator.netinfo()['nics'].keys()
    return lldp_info.get_info(filter)


def is_ovn_configured():
    """
    Check if OVN is configured on this host.
    :return: True if OVN is configured on this host
    """
    return ovn.is_ovn_configured()


def is_dhcp_ip_monitored(iface, family):
    """
    Check if combination of iface and family is configured for DHCP
    IP monitoring
    :param iface: Interface name
    :param family: 4 (IPv4) or 6 (IPv6)
    :return: True if the combination is monitored for DHCP IP changes
    """
    return MonitoredItemPool.instance().is_item_in_pool((iface, family))


def add_dynamic_source_route_rules(iface, address, prefixlen):
    """
    Delegate creation of dynamic source route rules to supervdsm context
    :param iface: Interface name
    :param address: Interface address
    :param prefixlen: Address prefixlen
    """
    nmstate_add_dynamic_source_route_rules(iface, address, prefixlen)


def remove_dhcp_monitoring(iface, family):
    """
    Remove iface and family combination from DHCP IP monitoring
    :param iface: Interface name
    :param family: 4 (IPv4) or 6 (IPv6)
    """
    MonitoredItemPool.instance().remove((iface, family))


@contextmanager
def wait_for_pci_link_up(pci_path, timeout):
    with sriov.wait_for_pci_link_up(pci_path, timeout):
        yield
