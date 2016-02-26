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
import errno
import os
import time
import logging


from vdsm.config import config
from vdsm import commands
from vdsm import constants
from vdsm import hooks
from vdsm import netconfpersistence
from vdsm.netinfo.cache import (libvirtNets2vdsm, get as netinfo_get,
                                CachingNetInfo)
from vdsm.netinfo import networks as netinfo_networks
from vdsm import udevadm
from vdsm import utils
from vdsm import ipwrapper

from . canonize import canonize_networks
from . import legacy_switch
from . import errors as ne
from .errors import ConfigNetworkError

CONNECTIVITY_TIMEOUT_DEFAULT = 4
_SYSFS_SRIOV_NUMVFS = '/sys/bus/pci/devices/{}/sriov_numvfs'


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
    logging.info("changing number of vfs on device %s -> %s.",
                 pci_path, numvfs)
    _update_numvfs(pci_path, numvfs)

    logging.info("changing number of vfs on device %s -> %s. succeeded.",
                 pci_path, numvfs)
    _persist_numvfs(pci_path, numvfs)

    ipwrapper.linkSet(net_name, ['up'])


def _buildSetupHookDict(req_networks, req_bondings, req_options):

    hook_dict = {'request': {'networks': dict(req_networks),
                             'bondings': dict(req_bondings),
                             'options': dict(req_options)}}

    return hook_dict


def _get_connectivity_timeout(options):
    return int(options.get('connectivityTimeout',
                           CONNECTIVITY_TIMEOUT_DEFAULT))


def _check_connectivity(networks, bondings, options, logger):
    if utils.tobool(options.get('connectivityCheck', True)):
        logger.debug('Checking connectivity...')
        if not _clientSeen(_get_connectivity_timeout(options)):
            logger.info('Connectivity check failed, rolling back')
            raise ConfigNetworkError(ne.ERR_LOST_CONNECTION,
                                     'connectivity check failed')


def _clientSeen(timeout):
    start = time.time()
    while timeout >= 0:
        try:
            if os.stat(constants.P_VDSM_CLIENT_LOG).st_mtime > start:
                return True
        except OSError as e:
            if e.errno == errno.ENOENT:
                pass  # P_VDSM_CLIENT_LOG is not yet there
            else:
                raise
        time.sleep(1)
        timeout -= 1
    return False


def _apply_hook(bondings, networks, options):
    results = hooks.before_network_setup(_buildSetupHookDict(networks,
                                                             bondings,
                                                             options))
    # gather any changes that could have been done by the hook scripts
    networks = results['request']['networks']
    bondings = results['request']['bondings']
    options = results['request']['options']
    return bondings, networks, options


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
                        (other options will be passed to the config file AS-IS)
                        -- OR --
                        remove=True (other attributes can't be specified)

        bondings - dict of key=bonding, value=attributes
            where 'attributes' is a dict with the following optional items:
                        nics=["<nic1>" , "<nic2>", ...]
                        options="<bonding-options>"
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
    logger = logging.getLogger("setupNetworks")

    logger.debug("Setting up network according to configuration: "
                 "networks:%r, bondings:%r, options:%r" % (networks,
                                                           bondings, options))

    canonize_networks(networks)
    # TODO: Add canonize_bondings(bondings)

    logging.debug("Validating configuration")
    legacy_switch.validateNetworkSetup(networks, bondings)

    bondings, networks, options = _apply_hook(bondings, networks, options)

    libvirt_nets = netinfo_networks()
    _netinfo = CachingNetInfo(_netinfo=netinfo_get(
        libvirtNets2vdsm(libvirt_nets)))

    logger.debug("Applying...")
    in_rollback = options.get('_inRollback', False)
    with legacy_switch.ConfiguratorClass(in_rollback) as configurator:
        # from this point forward, any exception thrown will be handled by
        # Configurator.__exit__.

        legacy_switch.remove_networks(networks, bondings, configurator,
                                      _netinfo, libvirt_nets, logger)

        legacy_switch.bonds_setup(bondings, configurator, _netinfo,
                                  in_rollback, logger)

        legacy_switch.add_missing_networks(configurator, networks, bondings,
                                           logger, _netinfo)

        _check_connectivity(networks, bondings, options, logger)

    hooks.after_network_setup(_buildSetupHookDict(networks, bondings, options))


def setSafeNetworkConfig():
    """Declare current network configuration as 'safe'"""
    commands.execCmd([constants.EXT_VDSM_STORE_NET_CONFIG,
                     config.get('vars', 'net_persistence')])
