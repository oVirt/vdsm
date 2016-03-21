#!/bin/env python
"""
FCoE hook:
   if fcoe = true custom networks was specified enable FCoE for specified NIC
syntax:
   fcoe = true|false
"""


import os
import traceback
import shutil

import six

import hooking
from vdsm import utils
from vdsm.netconfpersistence import RunningConfig


FCOE_CONFIG_DIR = '/etc/fcoe/'
FCOE_DEFAULT_CONFIG = os.path.join(FCOE_CONFIG_DIR, 'cfg-ethx')


def _has_fcoe(net_attr):
    """
    Check if fcoe parameter was specified as custom network property
    """
    return hooking.tobool(net_attr.get('custom', {}).get('fcoe'))


def _get_config_name(nic):
    """
    helper to return filename of configuration file
    """
    return os.path.join(FCOE_CONFIG_DIR, 'cfg-%s' % nic)


def _configure(interface):
    """
    Enable FCoE on specified interface by coping default configuration
    """
    filename = _get_config_name(interface)
    if not os.path.exists(filename):
        shutil.copyfile(FCOE_DEFAULT_CONFIG,
                        filename)
        utils.persist(filename)


def _unconfigure(interface):
    """
    Remove config file for specified interface
    """
    filename = _get_config_name(interface)
    if os.path.exists(filename):
        utils.unpersist(filename)
        utils.rmFile(filename)


def _all_configured_fcoe_networks():
    """
    Return a mapping of configured fcoe networks in format
    (network_name, nic_name)
    """
    existing_fcoe_networks = {}
    config = RunningConfig()
    for net, net_attr in six.iteritems(config.networks):
        if _has_fcoe(net_attr):
            nic = net_attr.get('nic')
            if nic:
                existing_fcoe_networks[net] = nic
            else:
                hooking.log("WARNING: Invalid FCoE configuration of %s "
                            "detected. Please check documentation" % (net))

    return existing_fcoe_networks


def _unconfigure_removed(configured, removed_networks):
    """
    Unconfigure fcoe if network was removed from the DC
    """
    for net, _ in six.iteritems(removed_networks):
        if net in configured:
            if configured[net] is not None:
                _unconfigure(configured[net])


def _unconfigure_non_fcoe(configured, changed_non_fcoe):
    """
    Unconfigure networks which are not longer has fcoe enabled
    Example:  Fcoe attribute was removed
    """
    for net, net_nic in six.iteritems(changed_non_fcoe):
        if net in configured and net_nic is not None:
            _unconfigure(net_nic)


def _reconfigure_fcoe(configured, changed_fcoe):
    """
    Configure all fcoe interfaces and unconfigure NIC which are not longer
    fcoe enabled
    Example: Moved from one NIC to another
    """
    for net, net_nic in six.iteritems(changed_fcoe):
        if net in configured and configured[net] != net_nic:
            _unconfigure(configured[net])
        if net_nic:
            _configure(net_nic)
        else:
            hooking.exit_hook("Failed to configure fcoe "
                              "on %s with no physical nic" % (net))


def main():
    """
    Create lists of running networks
    and networks to be (un)configured as FCoE or removed.
    """
    existing_fcoe_networks = _all_configured_fcoe_networks()

    changed_fcoe = {}
    changed_non_fcoe = {}
    removed_networks = {}

    setup_nets_config = hooking.read_json()
    changed_all = setup_nets_config['request']['networks']

    for net, net_attr in six.iteritems(changed_all):
        if _has_fcoe(net_attr):
            changed_fcoe[net] = net_attr.get('nic')
        elif hooking.tobool(net_attr.get('remove')):
            removed_networks[net] = net_attr.get('nic')
        else:
            changed_non_fcoe[net] = net_attr.get('nic')

    _unconfigure_removed(existing_fcoe_networks, removed_networks)
    _unconfigure_non_fcoe(existing_fcoe_networks, changed_non_fcoe)
    _reconfigure_fcoe(existing_fcoe_networks, changed_fcoe)

    # TODO If services are failed to start restore previous configuration
    # and notify user
    ret, _, err = hooking.execCmd(['/bin/systemctl', 'restart', 'lldpad'])
    if ret:
        hooking.log('Failed to restart lldpad service. err = %s' % (err))

    ret, _, err = hooking.execCmd(['/bin/systemctl', 'restart', 'fcoe'])
    if ret:
        hooking.log('Failed to restart fcoe service. err = %s' % (err))

if __name__ == '__main__':
    try:
        main()
    except:
        hooking.exit_hook(traceback.format_exc())
