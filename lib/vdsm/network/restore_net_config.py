# Copyright 2011-2020 Red Hat, Inc.
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

import glob
import itertools
import logging
import os
import re
import time
import errno

import six

from vdsm.common.config import config
from vdsm.common.time import monotonic_time
from vdsm.network import ipwrapper
from vdsm.network import kernelconfig
from vdsm.network import netswitch
from vdsm.network import sysctl
from vdsm.network.ip.address import ipv6_supported
from vdsm.network.link import sriov
from vdsm.network.netinfo import nics, misc
from vdsm.network.netinfo.cache import NetInfo
from vdsm.network.netrestore import NETS_RESTORED_MARK
from vdsm.network.netconfpersistence import (
    RunningConfig,
    PersistentConfig,
    BaseConfig,
)
from vdsm.network.nm import networkmanager

# Ifcfg persistence restoration
from vdsm.network.configurators import ifcfg

# Unified persistence restoration
from vdsm.network.api import setupNetworks, change_numvfs


_ALL_DEVICES_UP_TIMEOUT = 5


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
            change_numvfs(sriov_devpci, numvfs, devname)
        except Exception:
            logging.exception(
                'Restoring VF configuration for device %s failed. '
                'Persisted nets built on this device will fail to restore.',
                devname,
            )


def ifcfg_restoration():
    configWriter = ifcfg.ConfigWriter()
    configWriter.restorePersistentBackup()


def unified_restoration():
    """
    Builds a setupNetworks command from the persistent configuration to set it
    as running configuration.
    """
    networkmanager.init()

    persistent_config = PersistentConfig()
    available_config = _filter_available(persistent_config)

    _verify_all_devices_are_up(list(_owned_ifcfg_files()))

    _wait_for_for_all_devices_up(
        itertools.chain(
            available_config.networks.keys(), available_config.bonds.keys()
        )
    )

    if ipv6_supported():
        _restore_disable_ipv6()

    classified_conf = _classify_nets_bonds_config(available_config)
    setup_nets, setup_bonds, remove_nets, remove_bonds = classified_conf

    logging.info(
        'Remove networks (%s) and bonds (%s).', remove_nets, remove_bonds
    )
    _greedy_setup_bonds(remove_bonds)
    _greedy_setup_nets(remove_nets)

    _restore_non_vdsm_net_devices()

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
            logging.exception('Failed to setup {}'.format(net))


def _greedy_setup_bonds(setup_bonds):
    for bond, bond_attr in six.iteritems(setup_bonds):
        try:
            setupNetworks(
                {},
                {bond: bond_attr},
                {'connectivityCheck': False, '_inRollback': True},
            )
        except Exception:
            logging.exception('Failed to setup {}'.format(bond))


def _verify_all_devices_are_up(owned_ifcfg_files):
    """REQUIRED_FOR upgrade from 4.16<=vdsm<=4.16.20
    Were ifcfg files were created with ONBOOT=no.
    """
    for ifcfg_file in owned_ifcfg_files:
        _upgrade_onboot(ifcfg_file)
    down_links = _get_links_with_state_down(
        [os.path.basename(name) for name in owned_ifcfg_files]
    )
    if down_links:
        logging.debug("Some of the devices are down (%s).", down_links)
        ifcfg.start_devices(owned_ifcfg_files)


def _upgrade_onboot(ifcfg_file):
    with open(ifcfg_file) as f:
        old_content = f.read()
    new_content = re.sub(
        '^ONBOOT=no$', 'ONBOOT=yes', old_content, flags=re.MULTILINE
    )
    if new_content != old_content:
        logging.debug("updating %s to ONBOOT=yes", ifcfg_file)
        with open(ifcfg_file, 'w') as f:
            f.write(new_content)


def _owned_ifcfg_files():
    for fpath in glob.iglob(misc.NET_CONF_DIR + '/*'):
        if not os.path.isfile(fpath):
            continue

        with open(fpath) as f:
            content = f.read()
        if _owned_ifcfg_content(content):
            yield fpath


def _restore_non_vdsm_net_devices():
    # addresses (BZ#1188251)
    configWriter = ifcfg.ConfigWriter()
    configWriter.restorePersistentBackup()


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

    available_nics = nics.nics()
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
    extra_bonds = {
        bond: {'remove': True}
        for bond in extra_bonds_names
        if _owned_ifcfg(bond)
    }

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


def _wait_for_for_all_devices_up(links):
    timeout = monotonic_time() + _ALL_DEVICES_UP_TIMEOUT
    down_links = _get_links_with_state_down(links)

    # TODO: use netlink monitor here might be more elegant (not available in
    # TODO: 3.5)
    while down_links and monotonic_time() < timeout:
        logging.debug("waiting for %s to be up.", down_links)
        time.sleep(1)
        down_links = _get_links_with_state_down(links)

    if down_links:
        logging.warning(
            "Not all devices are up. VDSM might restore them "
            "although they were not changed since they were "
            "persisted."
        )
    else:
        logging.debug("All devices are up.")


def _get_links_with_state_down(links):
    return set(
        l.name
        for l in ipwrapper.getLinks()
        if l.name in links
        and _owned_ifcfg(l.name)
        and _onboot_ifcfg(l.name)
        and not l.oper_up
    )


def _ifcfg_dev_name(file_name):
    """Return the device name from the full path to its ifcfg- file."""
    return os.path.basename(file_name)[6:]


def _ipv6_ifcfg(link_name):
    def ipv6init(content):
        return all(line != 'IPV6INIT=no' for line in content.splitlines())

    return _ifcfg_predicate(link_name, ipv6init)


def _restore_disable_ipv6():
    """
    Disable IPv6 on networks with no IPv6 configuration. This must be done even
    before actual restoration is performed because there is probably going to
    be a link-local address already (or "worse", initscripts may have acquired
    a global address through autoconfiguration) and thus the network's state
    would differ from the persisted config, causing needless restoration.
    This is implemented for unified persistence only.
    """
    for filename in _owned_ifcfg_files():
        device = _ifcfg_dev_name(filename)
        if not _ipv6_ifcfg(device):
            try:
                sysctl.disable_ipv6(device)
            except IOError as e:
                if e.errno == errno.ENOENT:
                    pass  # the network is broken, but we have to handle it
                else:
                    raise


def _owned_ifcfg(link_name):
    return _ifcfg_predicate(link_name, _owned_ifcfg_content)


def _onboot_ifcfg(link_name):
    predicate = lambda content: any(
        line == 'ONBOOT=yes' for line in content.splitlines()
    )
    return _ifcfg_predicate(link_name, predicate)


def _owned_ifcfg_content(content):
    return content.startswith(
        '# Generated by VDSM version'
    ) or content.startswith('# automatically generated by vdsm')


def _ifcfg_predicate(link_name, predicate):
    try:
        with open(misc.NET_CONF_PREF + link_name) as conf:
            content = conf.read()
    except IOError as ioe:
        if ioe.errno == errno.ENOENT:
            return False
        else:
            raise
    else:
        return predicate(content)


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
    unified = config.get('vars', 'net_persistence') == 'unified'
    logging.info('starting network restoration.')
    try:
        if unified:
            unified_restoration()
        else:
            ifcfg_restoration()
            _copy_persistent_over_running_config()
    except Exception:
        logging.exception(
            '%s restoration failed.', 'unified' if unified else 'ifcfg'
        )
        raise
    else:
        logging.info('restoration completed successfully.')

    touch_file(NETS_RESTORED_MARK)


def _copy_persistent_over_running_config():
    pconfig = PersistentConfig()
    rconfig = RunningConfig()
    rconfig.delete()
    rconfig.networks = pconfig.networks
    rconfig.bonds = pconfig.bonds
    rconfig.save()
