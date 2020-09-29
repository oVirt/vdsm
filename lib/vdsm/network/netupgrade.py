# Copyright 2017-2020 Red Hat, Inc.
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

import os
import shutil
import string

import six

from vdsm.network.canonicalize import canonicalize_bondings
from vdsm.network.canonicalize import canonicalize_networks
from vdsm.network.configurators.ifcfg import ConfigWriter
from vdsm.network.kernelconfig import KernelConfig
from vdsm.network.link import sriov
from vdsm.network.netconfpersistence import RunningConfig, PersistentConfig
from vdsm.network.netinfo.cache import NetInfo, libvirt_vdsm_nets
from vdsm.network.netswitch.configurator import netinfo
from vdsm.network.ovs import info as ovs_info
from vdsm.network.ovs import switch as ovs_switch


try:
    from vdsm.virt import libvirtnetwork
except ImportError:
    # Virt package is not available, therefore, libvirt networks are not
    # relevant for the upgrade path.
    # Mocking libvirtnetwork.
    class LibvirtNetworkMock(object):
        def networks(self):
            return {}

        def removeNetwork(self, net):
            pass

    libvirtnetwork = LibvirtNetworkMock()


def upgrade():
    rconfig = RunningConfig()
    pconfig = PersistentConfig()

    libvirt_networks = libvirtnetwork.networks()

    _upgrade_volatile_running_config(rconfig)

    if rconfig.config_exists() or pconfig.config_exists():
        _upgrade_unified_configuration(rconfig)
        _upgrade_unified_configuration(pconfig)
    else:
        # In case unified config has not existed before, it is assumed that
        # the networks existance have been persisted in libvirt db.
        vdsmnets = libvirt_vdsm_nets(libvirt_networks)
        _create_unified_configuration(rconfig, NetInfo(netinfo(vdsmnets)))

    _cleanup_libvirt_networks(libvirt_networks)

    if ovs_info.is_ovs_service_running():
        ovs_switch.update_network_to_bridge_mappings(ovs_info.OvsInfo())


def _upgrade_volatile_running_config(rconfig):
    """
    Relocate the volatile version of running config (if exists)
    to the persisted version.
    This procedure is required in order to support upgrades to the new
    persisted version of running config.
    """
    if not rconfig.config_exists():
        volatile_rconfig = RunningConfig(volatile=True)
        if volatile_rconfig.config_exists():
            rconfig.networks = volatile_rconfig.networks
            rconfig.bonds = volatile_rconfig.bonds
            rconfig.save()
            volatile_rconfig.delete()


def _upgrade_unified_configuration(config):
    """
    Process an unified configuration file and normalize it to an up do date
    format.
    """
    save_changes = False

    if config.networks:
        _normalize_net_address(config.networks)
        _normalize_net_ifcfg_keys(config.networks)

        canonicalize_networks(config.networks)
        canonicalize_bondings(config.bonds)

        save_changes = True

    # Upgrading based on the persisted (safe) configuration.
    old_sriov_confpath = os.path.join(config.netconf_path, 'virtual_functions')
    if os.path.exists(old_sriov_confpath):
        _upgrade_sriov_config(config.devices, old_sriov_confpath)
        save_changes = True

    if save_changes:
        config.save()


def _normalize_net_address(networks):
    for net_name, net_attr in six.viewitems(networks):
        if 'defaultRoute' not in net_attr:
            net_attr['defaultRoute'] = net_name in ('ovirtmgmt', 'rhevm')


def _normalize_net_ifcfg_keys(networks):
    """
    Ignore keys in persisted networks that might originate from vdsm-reg.
    these might be a result of calling setupNetworks with ifcfg values
    that come from the original interface that is serving the management
    network. for 3.5, VDSM still supports passing arbitrary values
    directly to the ifcfg files, e.g. 'IPV6_AUTOCONF=no'.
    We filter them out here since they are not supported anymore.
    """
    for netname, netattrs in six.viewitems(networks):
        networks[netname] = {
            k: v
            for k, v in six.viewitems(netattrs)
            if not _is_unsupported_ifcfg_key(k)
        }


def _upgrade_sriov_config(devices, old_sriov_confpath):

    old_config = sriov.get_old_persisted_devices_numvfs(old_sriov_confpath)
    new_config = sriov.upgrade_devices_sriov_config(old_config)

    for devname in new_config:
        devices.setdefault(devname, {}).update(new_config[devname])

    shutil.rmtree(old_sriov_confpath)


def _is_unsupported_ifcfg_key(key):
    return set(key) <= set(string.ascii_uppercase + string.digits + '_')


def _create_unified_configuration(rconfig, net_info):
    """
    Given netinfo report, generate unified configuration and persist it.

    Networks and Bonds detected by the network caps/netinfo are recorded.
    In case there are external bonds (not owned), they are still counted for in
    this regard.

    Note: Unified persistence mode is not activated in this context, it is left
    as a separate step/action.
    """
    kconfig = KernelConfig(net_info)

    rconfig.networks = kconfig.networks
    rconfig.bonds = {}

    rconfig.save()
    ConfigWriter.clearBackups()
    RunningConfig.store()


def _cleanup_libvirt_networks(libvirt_networks):
    """
    Host networks are no longer persisted in libvirt db, therefore, they are
    removed as part of the upgrade.
    Note: The role of managing libvirt networks has passed to virt.
    """
    for net in libvirt_networks:
        libvirtnetwork.removeNetwork(net)
