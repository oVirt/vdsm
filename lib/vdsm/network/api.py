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
import errno
import os
import sys
import time
import logging

import six

from vdsm.config import config
from vdsm import commands
from vdsm import constants
from vdsm import hooks
from vdsm import netconfpersistence
from vdsm import udevadm
from vdsm import ipwrapper

from . canonicalize import canonicalize_networks
from . import netswitch
from .configurators import RollbackIncomplete

_SYSFS_SRIOV_NUMVFS = '/sys/bus/pci/devices/{}/sriov_numvfs'


def caps_networks():
    """Complement existing non-root caps"""
    return {}


def _wait_for_udev_events():
    # FIXME: This is an ugly hack that is meant to prevent VDSM to report VFs
    # that are not yet named by udev or not report all of. This is a blocking
    # call that should wait for all udev events to be handled. a proper fix
    # should be registering and listening to the proper netlink and udev
    # events. The sleep prior to observing udev is meant to decrease the
    # chances that we wait for udev before it knows from the kernel about the
    # new devices.
    time.sleep(0.5)
    udevadm.settle(timeout=10)


def _update_numvfs(pci_path, numvfs):
    """pci_path is a string looking similar to "0000:00:19.0"
    """
    with open(_SYSFS_SRIOV_NUMVFS.format(pci_path), 'w', 0) as f:
        # Zero needs to be written first in order to remove previous VFs.
        # Trying to just write the number (if n > 0 VF's existed before)
        # results in 'write error: Device or resource busy'
        # https://www.kernel.org/doc/Documentation/PCI/pci-iov-howto.txt
        f.write('0')
        f.write(str(numvfs))
        _wait_for_udev_events()


def _persist_numvfs(device_name, numvfs):
    dir_path = os.path.join(netconfpersistence.CONF_RUN_DIR,
                            'virtual_functions')
    try:
        os.makedirs(dir_path)
    except OSError as ose:
        if errno.EEXIST != ose.errno:
            raise
    with open(os.path.join(dir_path, device_name), 'w') as f:
        f.write(str(numvfs))


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
    _update_numvfs(pci_path, numvfs)

    logging.info('Changing number of vfs on device %s -> %s. succeeded.',
                 pci_path, numvfs)
    _persist_numvfs(pci_path, numvfs)

    ipwrapper.linkSet(net_name, ['up'])


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
                        netmask="<ipv4>"
                        gateway="<ipv4>"
                        bootproto="..."
                        ipv6addr="<ipv6>[/<prefixlen>]"
                        ipv6gateway="<ipv6>"
                        ipv6autoconf="0|1"
                        dhcpv6="0|1"
                        defaultRoute=True|False
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
    canonicalize_networks(networks)
    # TODO: Add canonicalize_bondings(bondings)

    logging.debug('Validating configuration')
    netswitch.validate(networks, bondings)

    bondings, networks, options = _apply_hook(bondings, networks, options)

    logging.debug('Applying...')
    in_rollback = options.get('_inRollback', False)
    with _rollback():
        netswitch.setup(networks, bondings, options, in_rollback)


def setSafeNetworkConfig():
    """Declare current network configuration as 'safe'"""
    commands.execCmd([constants.EXT_VDSM_STORE_NET_CONFIG,
                     config.get('vars', 'net_persistence')])
