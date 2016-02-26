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
from functools import wraps
import logging
import os

import six

from vdsm.config import config
from vdsm import ipwrapper
from vdsm import kernelconfig
from vdsm import netconfpersistence
from vdsm.netinfo import NET_PATH
from vdsm.netinfo import addresses
from vdsm.netinfo import bridges
from vdsm.netinfo import mtus
from vdsm.netinfo import nics as netinfo_nics
from vdsm.netinfo.cache import CachingNetInfo
from vdsm import utils

from .configurators import libvirt, dhclient
from .models import Bond, Bridge, IPv4, IPv6, Nic, Vlan
from .models import hierarchy_backing_device
from . import errors as ne
from .errors import ConfigNetworkError


def _getPersistenceModule():
    persistence = config.get('vars', 'net_persistence')
    if persistence == 'unified':
        return netconfpersistence
    else:
        from .configurators import ifcfg
        return ifcfg


def _getConfiguratorClass():
    configurator = config.get('vars', 'net_configurator')
    if configurator == 'iproute2':
        from .configurators.iproute2 import Iproute2
        return Iproute2
    elif configurator == 'pyroute2':
        try:
            from .configurators.pyroute_two import PyrouteTwo
            return PyrouteTwo
        except ImportError:
            logging.error('pyroute2 library for %s configurator is missing. '
                          'Use ifcfg instead.', configurator)
            from .configurators.ifcfg import Ifcfg
            return Ifcfg

    else:
        if configurator != 'ifcfg':
            logging.warn('Invalid config for network configurator: %s. '
                         'Use ifcfg instead.', configurator)
        from .configurators.ifcfg import Ifcfg
        return Ifcfg


_persistence = _getPersistenceModule()
ConfiguratorClass = _getConfiguratorClass()


def _objectivizeNetwork(bridge=None, vlan=None, vlan_id=None, bonding=None,
                        nic=None, mtu=None, ipaddr=None,
                        netmask=None, gateway=None, bootproto=None,
                        ipv6addr=None, ipv6gateway=None, ipv6autoconf=None,
                        dhcpv6=None, defaultRoute=None, _netinfo=None,
                        configurator=None, blockingdhcp=None,
                        opts=None):
    """
    Constructs an object hierarchy that describes the network configuration
    that is passed in the parameters.

    :param bridge: name of the bridge.
    :param vlan: vlan device name.
    :param vlan_id: vlan tag id.
    :param bonding: name of the bond.
    :param nic: name of the nic.
    :param mtu: the desired network maximum transmission unit.
    :param ipaddr: IPv4 address in dotted decimal format.
    :param netmask: IPv4 mask in dotted decimal format.
    :param gateway: IPv4 address in dotted decimal format.
    :param bootproto: protocol for getting IP config for the net, e.g., 'dhcp'
    :param ipv6addr: IPv6 address in format address[/prefixlen].
    :param ipv6gateway: IPv6 address in format address[/prefixlen].
    :param ipv6autoconf: whether to use IPv6's stateless autoconfiguration.
    :param dhcpv6: whether to use DHCPv6.
    :param _netinfo: network information snapshot.
    :param configurator: instance to use to apply the network configuration.
    :param blockingdhcp: whether to acquire dhcp IP config in a synced manner.
    :param defaultRoute: Should this network's gateway be set in the main
                         routing table?
    :param opts: misc options received by the callee, e.g., {'stp': True}. this
                 function can modify the dictionary.

    :returns: the top object of the hierarchy.
    """
    if configurator is None:
        configurator = ConfiguratorClass()
    if _netinfo is None:
        _netinfo = CachingNetInfo()
    if opts is None:
        opts = {}
    if bootproto == 'none':
        bootproto = None

    topNetDev = None
    if bonding:
        topNetDev = Bond.objectivize(bonding, configurator, options=None,
                                     nics=None, mtu=mtu, _netinfo=_netinfo,
                                     on_removal_just_detach_from_network=True)
    elif nic:
        bond = _netinfo.getBondingForNic(nic)
        if bond:
            raise ConfigNetworkError(ne.ERR_USED_NIC, 'nic %s already '
                                     'enslaved to %s' % (nic, bond))
        topNetDev = Nic(nic, configurator, mtu=mtu, _netinfo=_netinfo)
    if vlan is not None:
        tag = _netinfo.vlans[vlan]['vlanid'] if vlan_id is None else vlan_id
        topNetDev = Vlan(topNetDev, tag, configurator, mtu=mtu, name=vlan)
    elif vlan_id is not None:
        topNetDev = Vlan(topNetDev, vlan_id, configurator, mtu=mtu)
    if bridge is not None:
        topNetDev = Bridge(bridge, configurator, port=topNetDev, mtu=mtu,
                           stp=opts.get('stp', None))
        # inherit DUID from the port's existing DHCP lease (BZ#1219429)
        if topNetDev.port and bootproto == 'dhcp':
            _inherit_dhcp_unique_identifier(topNetDev, _netinfo)
    if topNetDev is None:
        raise ConfigNetworkError(ne.ERR_BAD_PARAMS, 'Network defined without '
                                 'devices.')
    topNetDev.ipv4 = IPv4(ipaddr, netmask, gateway, defaultRoute, bootproto)
    topNetDev.ipv6 = IPv6(ipv6addr, ipv6gateway, defaultRoute, ipv6autoconf,
                          dhcpv6)
    topNetDev.blockingdhcp = (configurator._inRollback or
                              utils.tobool(blockingdhcp))
    return topNetDev


def _inherit_dhcp_unique_identifier(bridge, _netinfo):
    """
    If there is dhclient already running on a bridge's port we have to use the
    same DHCP unique identifier (DUID) in order to get the same IP address.
    """
    # On EL7 dhclient doesn't have a -df option (to read DUID from the port's
    # lease file). We must detect if the option is available, by running
    # dhclient manually. To unbreak a beta4 release we just won't use the -df
    # option in that case. A proper fix is probably to fall back to -lf,
    # passing to it a modified NIC lease file.
    if not dhclient.supports_duid_file():
        return

    for devices in (_netinfo.nics, _netinfo.bondings, _netinfo.vlans):
        port = devices.get(bridge.port.name)
        if port and port['dhcpv4']:
            bridge.duid_source = bridge.port.name
            break


def _alterRunningConfig(func):
    """
    Wrapper for _addNetwork and _delNetwork that abstracts away all current
    configuration handling from the wrapped methods.
    """

    @wraps(func)
    def wrapped(network, configurator, **kwargs):
        if config.get('vars', 'net_persistence') == 'unified':
            if func.__name__ == '_delNetwork':
                configurator.runningConfig.removeNetwork(network)
            else:
                configurator.runningConfig.setNetwork(network, kwargs)
        return func(network, configurator, **kwargs)
    return wrapped


@_alterRunningConfig
def _addNetwork(network, configurator,
                vlan=None, bonding=None, nic=None, ipaddr=None,
                netmask=None, prefix=None, mtu=None, gateway=None, dhcpv6=None,
                ipv6addr=None, ipv6gateway=None, ipv6autoconf=None,
                bridged=True, _netinfo=None, hostQos=None,
                defaultRoute=None, blockingdhcp=False, **options):
    if _netinfo is None:
        _netinfo = CachingNetInfo()
    if dhcpv6 is not None:
        dhcpv6 = utils.tobool(dhcpv6)
    if ipv6autoconf is not None:
        ipv6autoconf = utils.tobool(ipv6autoconf)

    if network == '':
        raise ConfigNetworkError(ne.ERR_BAD_BRIDGE,
                                 'Empty network names are not valid')
    if prefix:
        if netmask:
            raise ConfigNetworkError(ne.ERR_BAD_PARAMS,
                                     'Both PREFIX and NETMASK supplied')
        else:
            try:
                netmask = addresses.prefix2netmask(int(prefix))
            except ValueError as ve:
                raise ConfigNetworkError(ne.ERR_BAD_ADDR, "Bad prefix: %s" %
                                         ve)

    logging.debug('validating network...')
    if network in _netinfo.networks:
        raise ConfigNetworkError(
            ne.ERR_USED_BRIDGE, 'Network already exists (%s)' % (network,))
    if bonding:
        _validateInterNetworkCompatibility(_netinfo, vlan, bonding)
    elif nic:
        _validateInterNetworkCompatibility(_netinfo, vlan, nic)

    logging.info('Adding network %s with vlan=%s, bonding=%s, nic=%s, '
                 'mtu=%s, bridged=%s, defaultRoute=%s, options=%s', network,
                 vlan, bonding, nic, mtu, bridged, defaultRoute, options)

    bootproto = options.pop('bootproto', None)

    net_ent = _objectivizeNetwork(
        bridge=network if bridged else None, vlan_id=vlan, bonding=bonding,
        nic=nic, mtu=mtu, ipaddr=ipaddr,
        netmask=netmask, gateway=gateway, bootproto=bootproto, dhcpv6=dhcpv6,
        blockingdhcp=blockingdhcp, ipv6addr=ipv6addr, ipv6gateway=ipv6gateway,
        ipv6autoconf=ipv6autoconf, defaultRoute=defaultRoute,
        _netinfo=_netinfo, configurator=configurator, opts=options)

    if bridged and network in _netinfo.bridges:
        # The bridge already exists, update the configured entity to one level
        # below and update the mtu of the bridge.
        # The mtu is updated in the bridge configuration and on all the tap
        # devices attached to it (for the VMs).
        # (expecting the bridge running mtu to be updated by the kernel when
        # the device attached under it has its mtu updated)
        logging.info("Bridge %s already exists.", network)
        net_ent_to_configure = net_ent.port
        _update_mtu_for_an_existing_bridge(network, configurator, mtu)
    else:
        net_ent_to_configure = net_ent

    if net_ent_to_configure is not None:
        logging.info("Configuring device %s", net_ent_to_configure)
        net_ent_to_configure.configure(**options)
    configurator.configureLibvirtNetwork(network, net_ent)
    if hostQos is not None:
        configurator.configureQoS(hostQos, net_ent)


def _validateInterNetworkCompatibility(ni, vlan, iface):
    iface_nets_by_vlans = dict((vlan, net)  # None is also a valid key
                               for (net, vlan)
                               in ni.getNetworksAndVlansForIface(iface))

    if vlan in iface_nets_by_vlans:
        raise ConfigNetworkError(ne.ERR_BAD_PARAMS,
                                 'interface %r cannot be defined with this '
                                 'network since it is already defined with '
                                 'network %s' % (iface,
                                                 iface_nets_by_vlans[vlan]))


def _update_mtu_for_an_existing_bridge(dev_name, configurator, mtu):
    if mtu != mtus.getMtu(dev_name):
        configurator.configApplier.setIfaceMtu(dev_name, mtu)
        _update_bridge_ports_mtu(dev_name, mtu)


def _update_bridge_ports_mtu(bridge, mtu):
    for port in bridges.ports(bridge):
        ipwrapper.linkSet(port, ['mtu', str(mtu)])


def _assertBridgeClean(bridge, vlan, bonding, nics):
    ports = set(bridges.ports(bridge))
    ifaces = set(nics)
    if vlan is not None:
        ifaces.add(vlan)
    else:
        ifaces.add(bonding)

    brifs = ports - ifaces

    if brifs:
        raise ConfigNetworkError(ne.ERR_USED_BRIDGE, 'bridge %s has interfaces'
                                 ' %s connected' % (bridge, brifs))


@_alterRunningConfig
def _delNetwork(network, configurator, vlan=None, bonding=None,
                bypassValidation=False,
                _netinfo=None, keep_bridge=False, **options):
    if _netinfo is None:
        _netinfo = CachingNetInfo()

    nics, vlan, vlan_id, bonding = _netinfo.getNicsVlanAndBondingForNetwork(
        network)
    bridged = _netinfo.networks[network]['bridged']

    logging.info("Removing network %s with vlan=%s, bonding=%s, nics=%s,"
                 "keep_bridge=%s options=%s", network, vlan, bonding,
                 nics, keep_bridge, options)

    if not bypassValidation:
        _validateDelNetwork(network, vlan, bonding, nics,
                            bridged and not keep_bridge, _netinfo)

    net_ent = _objectivizeNetwork(bridge=network if bridged else None,
                                  vlan=vlan, vlan_id=vlan_id, bonding=bonding,
                                  nic=nics[0] if nics and not bonding
                                  else None,
                                  _netinfo=_netinfo,
                                  configurator=configurator)
    net_ent.ipv4.bootproto = (
        'dhcp' if _netinfo.networks[network]['dhcpv4'] else 'none')

    if bridged and keep_bridge:
        # we now leave the bridge intact but delete everything underneath it
        net_ent_to_remove = net_ent.port
        if net_ent_to_remove is not None:
            # the configurator will not allow us to remove a bridge interface
            # (be it vlan, bond or nic) unless it is not used anymore. Since
            # we are interested to leave the bridge here, we have to disconnect
            # it from the device so that the configurator will allow its
            # removal.
            _disconnect_bridge_port(net_ent_to_remove.name)
    else:
        net_ent_to_remove = net_ent

    # We must first remove the libvirt network and then the network entity.
    # Otherwise if we first remove the network entity while the libvirt
    # network is still up, the network entity (In some flows) thinks that
    # it still has users and thus does not allow its removal
    configurator.removeLibvirtNetwork(network)
    if net_ent_to_remove is not None:
        logging.info('Removing network entity %s', net_ent_to_remove)
        net_ent_to_remove.remove()
    # We must remove the QoS last so that no devices nor networks mark the
    # QoS as used
    backing_device = hierarchy_backing_device(net_ent)
    if (backing_device is not None and
            os.path.exists(NET_PATH + '/' + backing_device.name)):
        configurator.removeQoS(net_ent)


def _validateDelNetwork(network, vlan, bonding, nics, bridge_should_be_clean,
                        _netinfo):
    if bonding:
        if set(nics) != set(_netinfo.bondings[bonding]["slaves"]):
            raise ConfigNetworkError(ne.ERR_BAD_NIC, '_delNetwork: %s are '
                                     'not all nics enslaved to %s' %
                                     (nics, bonding))
    if bridge_should_be_clean:
        _assertBridgeClean(network, vlan, bonding, nics)


def _disconnect_bridge_port(port):
    ipwrapper.linkSet(port, ['nomaster'])


def remove_networks(networks, bondings, configurator, _netinfo,
                    libvirt_nets, logger):
    kernel_config = kernelconfig.KernelConfig(_netinfo)
    normalized_config = kernelconfig.normalize(
        netconfpersistence.BaseConfig(networks, bondings))

    for network, attrs in networks.items():
        if network in _netinfo.networks:
            logger.debug("Removing network %r", network)
            keep_bridge = _should_keep_bridge(
                network_attrs=normalized_config.networks[network],
                currently_bridged=_netinfo.networks[network]['bridged'],
                net_kernel_config=kernel_config.networks[network]
            )

            _delNetwork(network, configurator,
                        _netinfo=_netinfo,
                        keep_bridge=keep_bridge)
            _netinfo.del_network(network)
            _netinfo.updateDevices()
        elif network in libvirt_nets:
            # If the network was not in _netinfo but is in the networks
            # returned by libvirt, it means that we are dealing with
            # a broken network.
            logger.debug('Removing broken network %r', network)
            _delBrokenNetwork(network, libvirt_nets[network],
                              configurator=configurator)
            _netinfo.updateDevices()
        elif 'remove' in attrs:
            raise ConfigNetworkError(ne.ERR_BAD_BRIDGE, "Cannot delete "
                                     "network %r: It doesn't exist in the "
                                     "system" % network)


def _delBrokenNetwork(network, netAttr, configurator):
    '''Adapts the network information of broken networks so that they can be
    deleted via _delNetwork.'''
    _netinfo = CachingNetInfo()
    _netinfo.networks[network] = netAttr
    _netinfo.networks[network]['dhcpv4'] = False

    if _netinfo.networks[network]['bridged']:
        try:
            nets = configurator.runningConfig.networks
        except AttributeError:
            nets = {}  # ifcfg does not need net definitions
        _netinfo.networks[network]['ports'] = _persistence.configuredPorts(
            nets, network)
    elif not os.path.exists('/sys/class/net/' + netAttr['iface']):
        # Bridgeless broken network without underlying device
        libvirt.removeNetwork(network)
        if config.get('vars', 'net_persistence') == 'unified':
            configurator.runningConfig.removeNetwork(network)
        return
    _delNetwork(network, configurator, bypassValidation=True,
                _netinfo=_netinfo)


def _should_keep_bridge(network_attrs, currently_bridged, net_kernel_config):
    marked_for_removal = 'remove' in network_attrs
    if marked_for_removal:
        return False

    should_be_bridged = network_attrs['bridged']
    if currently_bridged and not should_be_bridged:
        return False

    # check if the user wanted to only reconfigure bridge itself (mtu is a
    # special case as it is handled automatically by the os)
    def _bridge_only_config(conf):
        return dict(
            (k, v) for k, v in conf.iteritems()
            if k not in ('bonding', 'nic', 'mtu', 'vlan'))

    def _bridge_reconfigured(current_net_conf, required_net_conf):
        return (_bridge_only_config(current_net_conf) !=
                _bridge_only_config(required_net_conf))

    if currently_bridged and _bridge_reconfigured(net_kernel_config,
                                                  network_attrs):
        logging.debug("the bridge is being reconfigured")
        return False

    return True


def add_missing_networks(configurator, networks, bondings, logger, _netinfo):
    # We need to use the newest host info
    _netinfo.updateDevices()

    for network, attrs in networks.iteritems():
        if 'remove' in attrs:
            continue

        bond = attrs.get('bonding')
        if bond:
            _check_bonding_availability(bond, bondings, _netinfo)

        logger.debug("Adding network %r", network)
        try:
            _addNetwork(network, configurator,
                        _netinfo=_netinfo, **attrs)
        except ConfigNetworkError as cne:
            if cne.errCode == ne.ERR_FAILED_IFUP:
                logger.debug("Adding network %r failed. Running "
                             "orphan-devices cleanup", network)
                _emergencyNetworkCleanup(network, attrs,
                                         configurator)
            raise

        _netinfo.updateDevices()  # Things like a bond mtu can change


def _emergencyNetworkCleanup(network, networkAttrs, configurator):
    """Remove all leftovers after failed setupNetwork"""
    _netinfo = CachingNetInfo()

    topNetDev = None
    if 'bonding' in networkAttrs:
        if networkAttrs['bonding'] in _netinfo.bondings:
            topNetDev = Bond.objectivize(networkAttrs['bonding'], configurator,
                                         options=None, nics=None, mtu=None,
                                         _netinfo=_netinfo)
    elif 'nic' in networkAttrs:
        if networkAttrs['nic'] in _netinfo.nics:
            topNetDev = Nic(networkAttrs['nic'], configurator,
                            _netinfo=_netinfo)
    if 'vlan' in networkAttrs and topNetDev:
        vlan_name = '%s.%s' % (topNetDev.name, networkAttrs['vlan'])
        if vlan_name in _netinfo.vlans:
            topNetDev = Vlan(topNetDev, networkAttrs['vlan'], configurator)
    if networkAttrs['bridged']:
        if network in _netinfo.bridges:
            topNetDev = Bridge(network, configurator, port=topNetDev)

    if topNetDev:
        topNetDev.remove()


def _check_bonding_availability(bond, bonds, _netinfo):
    # network's bond must be a newly-built bond or previously-existing ones
    newly_built = bond in bonds and not bonds[bond].get('remove', False)
    already_existing = bond in _netinfo.bondings
    if not newly_built and not already_existing:
        raise ConfigNetworkError(
            ne.ERR_BAD_PARAMS, 'Bond %s does not exist' % bond)


def bonds_setup(bonds, configurator, _netinfo, in_rollback, logger):
    logger.debug('Starting bondings setup. bonds=%s, in_rollback=%s',
                 bonds, in_rollback)
    _netinfo.updateDevices()
    remove, edit, add = _bonds_classification(bonds, _netinfo)
    _bonds_remove(remove, configurator, _netinfo, in_rollback, logger)
    _bonds_edit(edit, configurator, _netinfo, logger)
    _bonds_add(add, configurator, _netinfo, logger)


def _bonds_classification(bonds, _netinfo):
    """
    Divide bondings according to whether they are to be removed, edited or
    added.
    """
    remove = set()
    edit = {}
    add = {}
    for name, attrs in six.iteritems(bonds):
        if 'remove' in attrs:
            remove.add(name)
        elif name in _netinfo.bondings:
            edit[name] = attrs
        else:
            add[name] = attrs
    return remove, edit, add


def _bonds_remove(bonds, configurator, _netinfo, in_rollback, logger):
    for name in bonds:
        if _is_bond_valid_for_removal(name, _netinfo, in_rollback, logger):
            bond = Bond.objectivize(
                name, configurator, options=None, nics=None, mtu=None,
                _netinfo=_netinfo)
            logger.debug('Removing bond %r', bond)
            bond.remove()
            _netinfo.del_bonding(name)


def _is_bond_valid_for_removal(bond, _netinfo, in_rollback, logger):
    if bond not in _netinfo.bondings:
        if in_rollback:
            logger.error('Cannot remove bonding %s during rollback: '
                         'does not exist', bond)
            return False
        else:
            raise ConfigNetworkError(ne.ERR_BAD_BONDING, 'Cannot remove '
                                     'bonding %s: does not exist' % bond)

    # Networks removal takes place before bondings handling, therefore all
    # assigned networks (bond_users) should be already removed.
    bond_users = _netinfo.ifaceUsers(bond)
    if bond_users:
        raise ConfigNetworkError(
            ne.ERR_USED_BOND, 'Cannot remove bonding %s: used by another '
            'interfaces %s' % (bond, bond_users))

    return True


def _bonds_edit(bonds, configurator, _netinfo, logger):
    _netinfo.updateDevices()

    for name, attrs in six.iteritems(bonds):
        slaves_to_remove = (set(_netinfo.bondings[name]['slaves']) -
                            set(attrs.get('nics')))
        logger.debug('Editing bond %r, removing slaves %s', name,
                     slaves_to_remove)
        _remove_slaves(slaves_to_remove, configurator, _netinfo)

    # we need bonds to be slaveless in _netinfo
    _netinfo.updateDevices()

    for name, attrs in six.iteritems(bonds):
        bond = Bond.objectivize(
            name, configurator, attrs.get('options'), attrs.get('nics'),
            mtu=None, _netinfo=_netinfo)
        logger.debug('Editing bond %r with options %s', bond, bond.options)
        configurator.editBonding(bond, _netinfo)


def _remove_slaves(slaves_to_remove, configurator, _netinfo):
    for name in slaves_to_remove:
        slave = Nic(name, configurator, _netinfo=_netinfo)
        slave.remove(remove_even_if_used=True)


def _bonds_add(bonds, configurator, _netinfo, logger):
    for name, attrs in six.iteritems(bonds):
        bond = Bond.objectivize(
            name, configurator, attrs.get('options'), attrs.get('nics'),
            mtu=None, _netinfo=_netinfo)
        logger.debug('Creating bond %r with options %s', bond, bond.options)
        configurator.configureBond(bond)


def validateNetworkSetup(networks, bondings):
    for network, networkAttrs in networks.iteritems():
        if networkAttrs.get('remove', False):
            _validate_network_remove(networkAttrs)
        elif 'vlan' in networkAttrs:
            Vlan.validateTag(networkAttrs['vlan'])

    currentNicsSet = set(netinfo_nics.nics())
    for bonding, bondingAttrs in bondings.iteritems():
        Bond.validateName(bonding)
        if 'options' in bondingAttrs:
            Bond.validateOptions(bondingAttrs['options'])

        if bondingAttrs.get('remove', False):
            continue

        nics = bondingAttrs.get('nics', None)
        if not nics:
            raise ConfigNetworkError(ne.ERR_BAD_PARAMS,
                                     "Must specify nics for bonding")
        if not set(nics).issubset(currentNicsSet):
            raise ConfigNetworkError(ne.ERR_BAD_NIC,
                                     "Unknown nics in: %r" % list(nics))


def _validate_network_remove(networkAttrs):
    net_attr_keys = set(networkAttrs)
    if 'remove' in net_attr_keys and net_attr_keys - set(
            ['remove', 'custom']):
        raise ConfigNetworkError(
            ne.ERR_BAD_PARAMS,
            "Cannot specify any attribute when removing (other "
            "than custom properties). specified attributes: %s" % (
                networkAttrs,))
