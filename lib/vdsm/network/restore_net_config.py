# Copyright 2011-2022 Red Hat, Inc.
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
from __future__ import division

import logging
import os

import six

from vdsm.common.constants import P_VDSM_RUN
from vdsm.network import ipwrapper
from vdsm.network import kernelconfig
from vdsm.network import netswitch
from vdsm.network.link import sriov
from vdsm.network.netinfo.cache import NetInfo
from vdsm.network.netconfpersistence import PersistentConfig, BaseConfig

# Unified persistence restoration
from vdsm.network.api import setupNetworks, change_numvfs

NETS_RESTORED_MARK = os.path.join(P_VDSM_RUN, 'nets_restored')


def _restore_sriov_config():
    persistent_config = PersistentConfig()

    current_sriov_pci_devs = sriov.list_sriov_pci_devices()
    desired_sriov_pci_devs = {
        sriov.devname2pciaddr(devname)
        for devname, devattrs in six.viewitems(persistent_config.devices)
        if 'sriov' in devattrs
    }

    non_persisted_devs = current_sriov_pci_devs - desired_sriov_pci_devs
    if non_persisted_devs:
        logging.info(
            'Non persisted SRIOV devices found: %s', non_persisted_devs
        )
    missing_current_devs = desired_sriov_pci_devs - current_sriov_pci_devs
    if missing_current_devs:
        logging.error(
            'Persisted SRIOV devices could not be found: %s',
            missing_current_devs,
        )

    for sriov_devpci in current_sriov_pci_devs & desired_sriov_pci_devs:
        devname = sriov.pciaddr2devname(sriov_devpci)
        numvfs = persistent_config.devices[devname]['sriov']['numvfs']
        try:
            change_numvfs(numvfs, devname)
        except Exception:
            logging.exception(
                'Restoring VF configuration for device %s failed. '
                'Persisted nets built on this device will fail to restore.',
                devname,
            )


def unified_restoration():
    """
    Builds a setupNetworks command from the persistent configuration to set it
    as running configuration.
    """
    persistent_config = PersistentConfig()
    available_config = _filter_available(persistent_config)

    classified_conf = _classify_nets_bonds_config(available_config)
    setup_nets, setup_bonds, remove_nets, remove_bonds = classified_conf

    logging.info(
        'Remove networks (%s) and bonds (%s).', remove_nets, remove_bonds
    )
    _greedy_setup_bonds(remove_bonds)
    _greedy_setup_nets(remove_nets)

    _convert_to_blocking_dhcp(setup_nets)
    logging.info(
        'Setup networks (%s) and bonds (%s).', setup_nets, setup_bonds
    )
    _greedy_setup_bonds(setup_bonds)
    _greedy_setup_nets(setup_nets)


def _greedy_setup_nets(setup_nets):
    for net, net_attr in six.iteritems(setup_nets):
        try:
            setupNetworks(
                {net: net_attr},
                {},
                {'connectivityCheck': False, '_inRollback': True},
            )
        except Exception:
            logging.exception('Failed to setup %s', net)


def _greedy_setup_bonds(setup_bonds):
    for bond, bond_attr in six.iteritems(setup_bonds):
        try:
            setupNetworks(
                {},
                {bond: bond_attr},
                {'connectivityCheck': False, '_inRollback': True},
            )
        except Exception:
            logging.exception('Failed to setup %s', bond)


def _convert_to_blocking_dhcp(networks):
    """
    This function changes DHCP configuration, if present, to be blocking.

    This is done right before restoring the network configuration, and forces
    the configurator to wait for an IP address to be configured on the devices
    before restoration is completed. This prevents VDSM to possibly report
    missing IP address on interfaces that had been restored right before it was
    started.
    """
    for net, net_attr in six.viewitems(networks):
        if net_attr.get('bootproto') == 'dhcp':
            net_attr['blockingdhcp'] = True


def _filter_available(persistent_config):
    """Returns only nets and bonds that can be configured with the devices
    present in the system"""

    available_nics = ipwrapper.visible_nics()
    available_bonds = _find_bonds_with_available_nics(
        available_nics, persistent_config.bonds
    )

    available_nets = _find_nets_with_available_devices(
        available_bonds,
        available_nics,
        persistent_config.bonds,
        persistent_config.networks,
    )
    return BaseConfig(available_nets, available_bonds, {})


def _classify_nets_bonds_config(persistent_config):
    """
    Return the configuration of networks and bonds, separating the ones changed
    and the ones unchanged:
    changed_nets, changed_bonds, unchanged_nets, unchanged_bonds
    """
    net_info = NetInfo(netswitch.configurator.netinfo())
    current_config = kernelconfig.KernelConfig(net_info)
    desired_config = kernelconfig.normalize(persistent_config)

    changed_nets_names, extra_nets_names = _classify_entities_difference(
        desired_config.networks, current_config.networks
    )

    changed_bonds_names, extra_bonds_names = _classify_entities_difference(
        desired_config.bonds, current_config.bonds
    )

    changed_nets = {
        net: persistent_config.networks[net] for net in changed_nets_names
    }
    changed_bonds = {
        bond: persistent_config.bonds[bond] for bond in changed_bonds_names
    }
    extra_nets = {net: {'remove': True} for net in extra_nets_names}
    # We cannot ensure which bond is being owned by us, so we should not
    # remove them
    # TODO This should be removed once the cleanup is done
    extra_bonds = {}

    return changed_nets, changed_bonds, extra_nets, extra_bonds


def _classify_entities_difference(desired, current):
    changed_or_missing = set()
    unchanged = set()
    for name, desired_attrs in six.viewitems(desired):
        current_attrs = current.get(name)
        if current_attrs != desired_attrs:
            changed_or_missing.add(name)
            logging.info(
                '%s is different or missing from persistent '
                'configuration. current: %s, persisted: %s',
                name,
                current_attrs,
                desired_attrs,
            )
        else:
            unchanged.add(name)
            logging.info(
                '%s was not changed since last time it was persisted,'
                ' skipping restoration.',
                name,
            )
    extra = set(current) - unchanged - changed_or_missing
    return changed_or_missing, extra


def _find_nets_with_available_devices(
    available_bonds, available_nics, persisted_bonds, persisted_nets
):
    available_nets = {}
    for net, attrs in six.viewitems(persisted_nets):
        bond = attrs.get('bonding')
        nic = attrs.get('nic')
        if bond is not None:
            if bond not in persisted_bonds:
                logging.error(
                    'Bond "%s" is not persisted and will not be '
                    'configured. Network "%s" will not be '
                    'configured as a consequence',
                    bond,
                    net,
                )
            elif bond not in available_bonds:
                logging.error(
                    'Some of the nics required by bond "%s" (%s) '
                    'are missing. Network "%s" will not be '
                    'configured as a consequence',
                    bond,
                    persisted_bonds[bond]['nics'],
                    net,
                )
            else:
                available_nets[net] = attrs

        elif nic is not None:
            if nic not in available_nics:
                logging.error(
                    'Nic "%s" required by network %s is missing. '
                    'The network will not be configured',
                    nic,
                    net,
                )
            else:
                available_nets[net] = attrs

        else:
            # nic-less network
            available_nets[net] = attrs

    return available_nets


def _find_bonds_with_available_nics(available_nics, persisted_bonds):
    available_bonds = {}
    for bond, attrs in six.viewitems(persisted_bonds):
        available_bond_nics = [
            nic for nic in attrs['nics'] if nic in available_nics
        ]
        if available_bond_nics:
            available_bonds[bond] = attrs.copy()
            available_bonds[bond]['nics'] = available_bond_nics
    return available_bonds


def _nets_already_restored(nets_restored_mark):
    return os.path.exists(nets_restored_mark)


def touch_file(file_path):
    with open(file_path, 'a'):
        os.utime(file_path, None)


def restore(force):
    if not force and _nets_already_restored(NETS_RESTORED_MARK):
        logging.info('networks already restored. doing nothing.')
        return

    _restore_sriov_config()
    logging.info('starting network restoration.')
    try:
        unified_restoration()
    except Exception:
        logging.exception('restoration failed.')
        raise
    else:
        logging.info('restoration completed successfully.')

    touch_file(NETS_RESTORED_MARK)
