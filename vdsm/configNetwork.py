# Copyright 2011-2012 Red Hat, Inc.
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

import sys
import os
import re
import traceback
import time
import logging
from contextlib import contextmanager
import socket
import struct


from vdsm import constants
from vdsm import utils
from storage.misc import execCmd
import neterrors as ne
from neterrors import ConfigNetworkError
from vdsm import define
from vdsm import netinfo
from vdsm.netinfo import DEFAULT_MTU
from netconf.ifcfg import ifup
from netconf.ifcfg import ifdown
from netconf.ifcfg import ConfigWriter

CONNECTIVITY_TIMEOUT_DEFAULT = 4
MAX_VLAN_ID = 4094
MAX_BRIDGE_NAME_LEN = 15
ILLEGAL_BRIDGE_CHARS = frozenset(':. \t')


def isBridgeNameValid(bridgeName):
    return (bridgeName and len(bridgeName) <= MAX_BRIDGE_NAME_LEN and
            len(set(bridgeName) & ILLEGAL_BRIDGE_CHARS) == 0 and
            not bridgeName.startswith('-'))


def validateBridgeName(bridgeName):
    if not isBridgeNameValid(bridgeName):
        raise ConfigNetworkError(ne.ERR_BAD_BRIDGE,
                                 "Bridge name isn't valid: %r" % bridgeName)


def _validateIpAddress(address):
    try:
        socket.inet_pton(socket.AF_INET, address)
    except socket.error:
        return False
    return True


def validateIpAddress(ipAddr):
    if not _validateIpAddress(ipAddr):
        raise ConfigNetworkError(ne.ERR_BAD_ADDR,
                                 "Bad IP address: %r" % ipAddr)


def validateNetmask(netmask):
    if not _validateIpAddress(netmask):
        raise ConfigNetworkError(ne.ERR_BAD_ADDR,
                                 "Bad netmask: %r" % netmask)

    num = struct.unpack('>I', socket.inet_aton(netmask))[0]
    if num & (num - 1) != (num << 1) & 0xffffffff:
        raise ConfigNetworkError(ne.ERR_BAD_ADDR, "Bad netmask: %r" % netmask)


def validateGateway(gateway):
    if not _validateIpAddress(gateway):
        raise ConfigNetworkError(ne.ERR_BAD_ADDR,
                                 "Bad gateway: %r" % gateway)


def validateBondingName(bonding):
    if not re.match('^bond[0-9]+$', bonding):
        raise ConfigNetworkError(ne.ERR_BAD_BONDING,
                                 '%r is not a valid bonding device name' %
                                 bonding)


def validateBondingOptions(bonding, bondingOptions):
    'Example: BONDING_OPTS="mode=802.3ad miimon=150"'
    with _validationBond(bonding) as bond:
        try:
            for option in bondingOptions.split():
                key, value = option.split('=')
                if not os.path.exists(
                        '/sys/class/net/%s/bonding/%s' % (bond, key)):
                    raise ConfigNetworkError(ne.ERR_BAD_BONDING, '%r is not a '
                                             'valid bonding option' % key)
        except ValueError:
            raise ConfigNetworkError(ne.ERR_BAD_BONDING, 'Error parsing '
                                     'bonding options: %r' % bondingOptions)


@contextmanager
def _validationBond(bonding):
    bond_created = False
    try:
        bonding = open(netinfo.BONDING_MASTERS, 'r').read().split()[0]
    except IndexError:
        open(netinfo.BONDING_MASTERS, 'w').write('+%s\n' % bonding)
        bond_created = True
    try:
        yield bonding
    finally:
        if bond_created:
            open(netinfo.BONDING_MASTERS, 'w').write('-%s\n' % bonding)


def validateVlanId(vlan):
    try:
        if not 0 <= int(vlan) <= MAX_VLAN_ID:
            raise ConfigNetworkError(
                ne.ERR_BAD_VLAN, 'vlan id out of range: %r, must be 0..%s' %
                (vlan, MAX_VLAN_ID))
    except ValueError:
        raise ConfigNetworkError(ne.ERR_BAD_VLAN, 'vlan id must be a number')


def _validateInterNetworkCompatibility(ni, vlan, iface, bridged):
    """
    Verify network compatibility with other networks on iface (bond/nic).

    Only following combinations allowed:
        - single non-VLANed bridged network
        - multiple VLANed networks (bridged/bridgeless) with only one
          non-VLANed bridgeless network
    """
    def _validateNoDirectNet(ifaces):
        # validate that none of the ifaces
        # is a non-VLANed network over our iface
        for (iface_net, iface_vlan) in ifaces:
            if iface_vlan is None:
                raise ConfigNetworkError(ne.ERR_BAD_PARAMS, 'interface %r '
                                         'already member of network %r' %
                                         (iface, iface_net))

    ifaces_bridgeless = tuple(ni.getBridgelessNetworksAndVlansForIface(iface))
    ifaces_bridged = tuple(ni.getBridgedNetworksAndVlansForIface(iface))

    # If non-VLANed bridged network exists
    # we can't add nothing else
    _validateNoDirectNet(ifaces_bridged)

    # Multiple VLANed networks (bridged/bridgeless) with only one
    # non-VLANed bridgeless network permited
    if not vlan:
        # Want to add non-VLANed bridgeless network,
        # check whether interface already has such network.
        # Only one non-VLANed bridgeless network permited
        if not bridged:
            _validateNoDirectNet(ifaces_bridgeless)
        # Want to add non-VLANed bridged network,
        # check whether interface is empty
        elif ifaces_bridged or ifaces_bridgeless:
            raise ConfigNetworkError(ne.ERR_BAD_PARAMS, 'interface %r already '
                                     'has networks' % (iface))


def _addNetworkValidation(_netinfo, network, vlan, bonding, nics, ipaddr,
                          netmask, gateway, bondingOptions, bridged=True,
                          implicitBonding=False, **options):
    # The (relatively) new setupNetwork verb allows to specify a network on
    # top of an existing bonding device. The nics of this bonds are taken
    # implictly from current host configuration
    if bonding and implicitBonding:
        pass
    elif (vlan or bonding) and not nics:
        raise ConfigNetworkError(ne.ERR_BAD_PARAMS, 'vlan/bonding definition '
                                 'requires nics. got: %r' % (nics,))

    # Check bridge
    if bridged:
        validateBridgeName(network)

    if network in _netinfo.networks:
        raise ConfigNetworkError(ne.ERR_USED_BRIDGE, 'Network already exists')

    # Check vlan
    if vlan:
        validateVlanId(vlan)

    # Check ip, netmask, gateway
    if ipaddr:
        if not netmask:
            raise ConfigNetworkError(ne.ERR_BAD_ADDR, 'Must specify netmask to'
                                     ' configure ip for network')
        validateIpAddress(ipaddr)
        validateNetmask(netmask)
        if gateway:
            validateGateway(gateway)
    else:
        if netmask or gateway:
            raise ConfigNetworkError(ne.ERR_BAD_ADDR,
                                     'Specified netmask or gateway but not ip')

    # Check bonding
    if bonding:
        validateBondingName(bonding)
        if bondingOptions:
            validateBondingOptions(bonding, bondingOptions)

        _validateInterNetworkCompatibility(_netinfo, vlan, bonding, bridged)
    elif bondingOptions:
        raise ConfigNetworkError(ne.ERR_BAD_BONDING,
                                 'Bonding options specified without bonding')
    elif len(nics) > 1:
        raise ConfigNetworkError(ne.ERR_BAD_BONDING,
                                 'Multiple nics require a bonding device')

    # Check nics
    for nic in nics:
        if nic not in _netinfo.nics:
            raise ConfigNetworkError(ne.ERR_BAD_NIC, "unknown nic: %r" % nic)

        # Make sure nics don't have a different bonding
        # still relevant if bonding is None
        bondingForNics = _netinfo.getBondingForNic(nic)
        if bondingForNics and bondingForNics != bonding:
            raise ConfigNetworkError(ne.ERR_USED_NIC,
                                     'nic %s already enslaved to %s' %
                                     (nic, bondingForNics))

        # Make sure nics don't used by vlans if bond requested
        if bonding:
            vlansForNic = tuple(_netinfo.getVlansForIface(nic))
            if vlansForNic:
                raise ConfigNetworkError(ne.ERR_USED_NIC,
                                         'nic %s already used by vlans %s' %
                                         (nic, vlansForNic))
            networkForNic = _netinfo.getNetworkForIface(nic)
            if networkForNic:
                raise ConfigNetworkError(ne.ERR_USED_NIC,
                                         'nic %s already used by network %s' %
                                         (nic, networkForNic))
        else:
            _validateInterNetworkCompatibility(_netinfo, vlan, nic, bridged)


def addNetwork(network, vlan=None, bonding=None, nics=None, ipaddr=None,
               netmask=None, mtu=None, gateway=None, force=False,
               configWriter=None, bondingOptions=None, bridged=True,
               **options):
    nics = nics or ()
    _netinfo = netinfo.NetInfo()
    bridged = utils.tobool(bridged)

    if mtu:
        mtu = int(mtu)

    prefix = options.get('prefix')
    if prefix is not None:
        if netmask is None:
            netmask = netinfo.prefix2netmask(int(prefix))
            del options['prefix']
        else:
            raise ConfigNetworkError(ne.ERR_BAD_PARAMS,
                                     'Both PREFIX and NETMASK supplied')

    # Validation
    if not utils.tobool(force):
        logging.debug('validating network...')
        _addNetworkValidation(_netinfo, network=network, vlan=vlan,
                              bonding=bonding, nics=nics, ipaddr=ipaddr,
                              netmask=netmask, gateway=gateway,
                              bondingOptions=bondingOptions, bridged=bridged,
                              **options)

    logging.info("Adding network %s with vlan=%s, bonding=%s, nics=%s,"
                 " bondingOptions=%s, mtu=%s, bridged=%s, options=%s",
                 network, vlan, bonding, nics, bondingOptions,
                 mtu, bridged, options)

    if configWriter is None:
        configWriter = ConfigWriter()

    prevmtu = None
    if mtu:
        prevmtu = configWriter.getMaxMtu(nics, mtu)

    nic = nics[0] if nics else None
    iface = bonding or nic
    blockingDhcp = utils.tobool(options.get('blockingdhcp'))

    # take down nics that need to be changed
    vlanedIfaces = [v['iface'] for v in _netinfo.vlans.values()]
    if bonding not in vlanedIfaces:
        for nic in nics:
            if nic not in vlanedIfaces:
                ifdown(nic)

    if bridged:
        configWriter.addBridge(network, ipaddr=ipaddr, netmask=netmask,
                               mtu=mtu, gateway=gateway, **options)
        ifdown(network)
        # We need to define (if requested) ip, mask & gateway on ifcfg-*
        # only on most top device according to following order:
        # bridge -> vlan -> bond -> nic
        # For lower level devices we should ignore it.
        # reset ip, netmask, gateway and bootproto for lower level devices
        bridgeBootproto = options.get('bootproto')
        ipaddr = netmask = gateway = options['bootproto'] = None

    # For VLAN we should attach bridge only to the VLAN device
    # rather than to underlying NICs or bond
    brName = network if bridged else None
    bridgeForNic = None if vlan else brName

    # We want to create config files (ifcfg-*) in top-down order
    # (bridge->vlan->bond->nic) to be able to handle IP/NETMASK
    # correctly for bridgeless networks
    if vlan:
        # don't ifup VLAN interface here, it should be done last,
        # after the bond and nic up
        configWriter.addVlan(vlan, iface, network=brName,
                             mtu=mtu, bridged=bridged,
                             ipaddr=ipaddr, netmask=netmask,
                             gateway=gateway, **options)
        iface += '.' + vlan
        vlanBootproto = options.get('bootproto')
        # reset ip, netmask, gateway and bootproto for lower level devices
        ipaddr = netmask = gateway = options['bootproto'] = None

    # First we need to prepare all conf files
    if bonding:
        configWriter.addBonding(bonding, bridge=bridgeForNic,
                                bondingOptions=bondingOptions,
                                mtu=max(prevmtu, mtu),
                                ipaddr=ipaddr, netmask=netmask,
                                gateway=gateway, **options)
        bondBootproto = options.get('bootproto')
        # reset ip, netmask, gateway and bootproto for lower level devices
        ipaddr = netmask = gateway = options['bootproto'] = None

    for nic in nics:
        configWriter.addNic(nic, bonding=bonding,
                            bridge=bridgeForNic if not bonding else None,
                            mtu=max(prevmtu, mtu), ipaddr=ipaddr,
                            netmask=netmask, gateway=gateway, **options)

    # Now we can run ifup for all interfaces
    if bonding:
        ifup(bonding, bondBootproto == 'dhcp' and not blockingDhcp)
    else:
        for nic in nics:
            ifup(nic, options.get('bootproto') == 'dhcp' and not blockingDhcp)

    # Now we can ifup VLAN interface, because bond and nic already up
    if vlan:
        ifup(iface, vlanBootproto == 'dhcp' and not blockingDhcp)

    if bridged:
        ifup(network, bridgeBootproto == 'dhcp' and not blockingDhcp)

    # add libvirt network
    configWriter.createLibvirtNetwork(network, bridged, iface)


def assertBridgeClean(bridge, vlan, bonding, nics):
    ports = set(netinfo.ports(bridge))
    ifaces = set(nics)
    if vlan:
        ifaces.add((bonding or nics[0]) + '.' + vlan)
    else:
        ifaces.add(bonding)

    brifs = ports - ifaces

    if brifs:
        raise ConfigNetworkError(ne.ERR_USED_BRIDGE, 'bridge %s has interfaces'
                                 ' %s connected' % (bridge, brifs))


def showNetwork(network):
    _netinfo = netinfo.NetInfo()
    if network not in _netinfo.networks:
        print "Network %r doesn't exist" % network
        return

    bridged = _netinfo.networks[network]['bridged']
    print "Network %s(Bridged: %s):" % (network, bridged)

    nics, vlan, bonding = _netinfo.getNicsVlanAndBondingForNetwork(network)

    if bridged:
        ipaddr = _netinfo.networks[network]['addr']
        netmask = _netinfo.networks[network]['netmask']
        gateway = _netinfo.networks[network]['gateway']
        print "ipaddr=%s, netmask=%s, gateway=%s" % (ipaddr, netmask, gateway)
    else:
        iface = _netinfo.networks[network]['iface']
        ipaddr = _netinfo.nics[iface]['addr']
        netmask = _netinfo.nics[iface]['netmask']
        print "ipaddr=%s, netmask=%s" % (ipaddr, netmask)

    print "vlan=%s, bonding=%s, nics=%s" % (vlan, bonding, nics)


def listNetworks():
    _netinfo = netinfo.NetInfo()
    print "Networks:", _netinfo.networks.keys()
    print "Vlans:", _netinfo.vlans.keys()
    print "Nics:", _netinfo.nics.keys()
    print "Bondings:", _netinfo.bondings.keys()


def _removeUnusedNics(network, vlan, bonding, nics, configWriter):
    _netinfo = netinfo.NetInfo()
    for nic in nics:
        if not _netinfo.nicOtherUsers(network, vlan, bonding, nic):
            ifdown(nic)
            configWriter.removeNic(nic)
            ifup(nic)


def _delBrokenNetwork(network, netAttr, configWriter):
    '''Adapts the network information of broken networks so that they can be
    deleted via delNetwork.'''
    _netinfo = netinfo.NetInfo()
    _netinfo.networks[network] = netAttr
    if _netinfo.networks[network]['bridged']:
        _netinfo.networks[network]['ports'] = ConfigWriter.ifcfgPorts(network)
    delNetwork(network, configWriter=configWriter, force=True,
               implicitBonding=False, _netinfo=_netinfo)


def delNetwork(network, vlan=None, bonding=None, nics=None, force=False,
               configWriter=None, implicitBonding=True, _netinfo=None,
               **options):
    if _netinfo is None:
        _netinfo = netinfo.NetInfo()

    if configWriter is None:
        configWriter = ConfigWriter()

    if network not in _netinfo.networks:
        logging.info("Network %r: doesn't exist in libvirt database", network)
        if network in netinfo.bridges():
            configWriter.removeBridge(network)
        else:
            raise ConfigNetworkError(ne.ERR_BAD_BRIDGE, "Cannot delete network"
                                     " %r: It doesn't exist in the system" %
                                     network)

        if vlan:
            configWriter.removeVlan(vlan, bonding or nics[0])

        return

    nics, vlan, bonding = _netinfo.getNicsVlanAndBondingForNetwork(network)
    bridged = _netinfo.networks[network]['bridged']

    logging.info("Removing network %s with vlan=%s, bonding=%s, nics=%s,"
                 "options=%s" % (network, vlan, bonding, nics, options))

    if not utils.tobool(force):
        if bonding:
            validateBondingName(bonding)
            if set(nics) != set(_netinfo.bondings[bonding]["slaves"]):
                raise ConfigNetworkError(ne.ERR_BAD_NIC, 'delNetwork: %s are '
                                         'not all nics enslaved to %s' %
                                         (nics, bonding))
        if vlan:
            validateVlanId(vlan)
        if bridged:
            assertBridgeClean(network, vlan, bonding, nics)

    configWriter.setNewMtu(network=network, bridged=bridged, _netinfo=_netinfo)
    configWriter.removeLibvirtNetwork(network)

    # We need to gather NetInfo again to refresh networks info from libvirt.
    # The deleted bridge should never be up at this stage.
    _netinfo = netinfo.NetInfo()
    if network in _netinfo.networks:
        raise ConfigNetworkError(ne.ERR_USED_BRIDGE, 'delNetwork: bridge %s '
                                 'still exists' % network)

    if network and bridged:
        configWriter.removeBridge(network)

    nic = nics[0] if nics else None
    iface = bonding if bonding else nic
    if iface:
        ifdown(iface)
        if vlan:
            configWriter.removeVlan(vlan, iface)
        else:
            cf = netinfo.NET_CONF_PREF + iface
            if not bridged:
                # When removing bridgeless non-VLANed network
                # we need to remove IP/NETMASK from the cfg file
                for key in ('IPADDR', 'NETMASK', 'GATEWAY', 'BOOTPROTO'):
                    configWriter._updateConfigValue(cf, key, '', True)
            else:
                # When removing bridged non-VLANed network
                # we need to remove BRIDGE from the cfg file
                configWriter._updateConfigValue(cf, 'BRIDGE', '', True)

        # Now we can restart changed interface
        ifup(iface)

    # The (relatively) new setupNetwork verb allows to remove a network
    # defined on top of an bonding device without break the bond itself.
    if implicitBonding:
        if bonding and not _netinfo.bondingOtherUsers(network, vlan, bonding):
            ifdown(bonding)
            configWriter.removeBonding(bonding)

        _removeUnusedNics(network, vlan, bonding, nics, configWriter)
    elif not bonding:
        _removeUnusedNics(network, vlan, bonding, nics, configWriter)
    elif not _netinfo.bondingOtherUsers(network, vlan, bonding):
        # update MTU for bond interface and underlying NICs
        ifdown(bonding)
        cf = netinfo.NET_CONF_PREF + bonding
        configWriter._updateConfigValue(cf, 'MTU', DEFAULT_MTU, False)
        for nic in nics:
            cf = netinfo.NET_CONF_PREF + nic
            configWriter._updateConfigValue(cf, 'MTU', DEFAULT_MTU, False)

        ifup(bonding)


def clientSeen(timeout):
    start = time.time()
    while timeout >= 0:
        if os.stat(constants.P_VDSM_CLIENT_LOG).st_mtime > start:
            return True
        time.sleep(1)
        timeout -= 1
    return False


def editNetwork(oldBridge, newBridge, vlan=None, bonding=None, nics=None,
                **options):
    configWriter = ConfigWriter()
    try:
        delNetwork(oldBridge, configWriter=configWriter, **options)
        addNetwork(newBridge, vlan=vlan, bonding=bonding, nics=nics,
                   configWriter=configWriter, **options)
    except:
        configWriter.restoreBackups()
        raise
    if utils.tobool(options.get('connectivityCheck', False)):
        if not clientSeen(int(options.get('connectivityTimeout',
                                          CONNECTIVITY_TIMEOUT_DEFAULT))):
            delNetwork(newBridge, force=True)
            configWriter.restoreBackups()
            return define.errCode['noConPeer']['status']['code']


def _validateNetworkSetup(networks={}, bondings={}):
    _netinfo = netinfo.NetInfo()

    for network, networkAttrs in networks.iteritems():
        if networkAttrs.get('remove', False):
            if set(networkAttrs) - set(['remove']):
                raise ConfigNetworkError(ne.ERR_BAD_PARAMS, 'Cannot specify '
                                         'any attribute when removing')

    for bonding, bondingAttrs in bondings.iteritems():
        validateBondingName(bonding)
        if 'options' in bondingAttrs:
            validateBondingOptions(bonding, bondingAttrs['options'])

        if bondingAttrs.get('remove', False):
            if bonding not in _netinfo.bondings:
                raise ConfigNetworkError(ne.ERR_BAD_BONDING, "Cannot remove "
                                         "bonding %s: Doesn't exist" % bonding)
            continue

        nics = bondingAttrs.get('nics', None)
        if not nics:
            raise ConfigNetworkError(ne.ERR_BAD_PARAMS,
                                     "Must specify nics for bonding")
        if not set(nics).issubset(set(_netinfo.nics)):
            raise ConfigNetworkError(ne.ERR_BAD_NIC,
                                     "Unknown nics in: %r" % list(nics))


def _editBondings(bondings, configWriter):
    """ Add/Edit bond interface """
    logger = logging.getLogger("_editBondings")

    _netinfo = netinfo.NetInfo()

    for bond, bondAttrs in bondings.iteritems():
        logger.debug("Creating/Editing bond %s with attributes %s",
                     bond, bondAttrs)

        bridge = _netinfo.getBridgedNetworkForIface(bond)

        mtu = None
        if bond in _netinfo.bondings:
            # Save MTU for future set on NICs
            confParams = netinfo.getIfaceCfg(bond)
            mtu = confParams.get('MTU', None)
            if mtu:
                mtu = int(mtu)

            ifdown(bond)
            # Take down all bond's NICs.
            for nic in _netinfo.getNicsForBonding(bond):
                ifdown(nic)
                configWriter.removeNic(nic)
                if nic not in bondAttrs['nics']:
                    ifup(nic)

        # Note! In case we have bridge up and connected to the bond
        # we will get error in log:
        #   (ifdown) bridge XXX is still up; can't delete it
        # But, we prefer this behaviour instead of taking bridge down
        # Anyway, we will not be able to take it down with connected VMs

        # First we need to prepare all conf files
        configWriter.addBonding(bond, bridge=bridge, mtu=mtu,
                                bondingOptions=bondAttrs.get('options', None))

        for nic in bondAttrs['nics']:
            configWriter.addNic(nic, bonding=bond, mtu=mtu)

        # Now we can run ifup for all interfaces
        ifup(bond)


def _removeBondings(bondings, configWriter):
    """ Remove bond interface """
    logger = logging.getLogger("_removeBondings")

    _netinfo = netinfo.NetInfo()

    for bond, bondAttrs in bondings.items():
        if 'remove' in bondAttrs:
            nics = _netinfo.getNicsForBonding(bond)
            logger.debug("Removing bond %r with nics = %s", bond, nics)
            ifdown(bond)
            configWriter.removeBonding(bond)

            for nic in nics:
                ifdown(nic)
                configWriter.removeNic(nic)
                ifup(nic)

            del bondings[bond]


def _buildBondOptions(bondName, bondings, _netinfo):
    logger = logging.getLogger("_buildBondOptions")

    bond = {}
    if bondings.get(bondName):
        bond['nics'] = bondings[bondName]['nics']
        bond['bondingOptions'] = bondings[bondName].get('options', None)
    elif bondName in _netinfo.bondings:
        # We may not receive any information about the bonding device if it is
        # unchanged. In this case check whether this bond exists on host and
        # take its parameters.
        logger.debug("Fetching bond %r info", bondName)
        existingBond = _netinfo.bondings[bondName]
        bond['nics'] = existingBond['slaves']
        bond['bondingOptions'] = existingBond['cfg'].get('BONDING_OPTS', None)
    else:
        raise ConfigNetworkError(ne.ERR_BAD_PARAMS, "No bonding option given, "
                                 "nor existing bond %s found." % bondName)
    return bond


def setupNetworks(networks={}, bondings={}, **options):
    """Add/Edit/Remove configuration for networks and bondings.

    Params:
        networks - dict of key=network, value=attributes
            where 'attributes' is a dict with the following optional items:
                        vlan=<id>
                        bonding="<name>" | nic="<name>"
                        (bonding and nics are mutually exclusive)
                        ipaddr="<ip>"
                        netmask="<ip>"
                        gateway="<ip>"
                        bootproto="..."
                        delay="..."
                        onboot="yes"|"no"
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
                        force=0|1
                        connectivityCheck=0|1
                        connectivityTimeout=<int>

    Notes:
        When you edit a network that is attached to a bonding, it's not
        necessary to re-specify the bonding (you need only to note
        the attachment in the network's attributes). Similarly, if you edit
        a bonding, it's not necessary to specify its networks.
    """
    logger = logging.getLogger("setupNetworks")
    _netinfo = netinfo.NetInfo()
    configWriter = ConfigWriter()
    networksAdded = set()

    logger.debug("Setting up network according to configuration: "
                 "networks:%r, bondings:%r, options:%r" % (networks,
                 bondings, options))

    force = options.get('force', False)
    if not utils.tobool(force):
        logging.debug("Validating configuration")
        _validateNetworkSetup(dict(networks), dict(bondings))

    logger.debug("Applying...")
    try:
        libvirt_nets = netinfo.networks()
        # Remove edited networks and networks with 'remove' attribute
        for network, networkAttrs in networks.items():
            if network in _netinfo.networks:
                logger.debug("Removing network %r" % network)
                delNetwork(network, configWriter=configWriter, force=force,
                           implicitBonding=False)
                if 'remove' in networkAttrs:
                    del networks[network]
                    del libvirt_nets[network]
            elif network in libvirt_nets:
                # If the network was not in _netinfo but is in the networks
                # returned by libvirt, it means that we are dealing with
                # a broken network.
                logger.debug('Removing broken network %r' % network)
                _delBrokenNetwork(network, libvirt_nets[network],
                                  configWriter=configWriter)
                if 'remove' in networkAttrs:
                    del networks[network]
                    del libvirt_nets[network]
            else:
                networksAdded.add(network)

        # Remove bonds with 'remove' attribute
        _removeBondings(bondings, configWriter)

        # Check whether bonds should be resized
        _editBondings(bondings, configWriter)

        # We need to use the newest host info
        _ni = netinfo.NetInfo()
        for network, networkAttrs in networks.iteritems():
            d = dict(networkAttrs)
            if 'bonding' in d:
                d.update(_buildBondOptions(d['bonding'], bondings, _ni))
            else:
                d['nics'] = [d.pop('nic')]
            d['force'] = force

            logger.debug("Adding network %r" % network)
            addNetwork(network, configWriter=configWriter,
                       implicitBonding=True, **d)

        if utils.tobool(options.get('connectivityCheck', True)):
            logger.debug('Checking connectivity...')
            if not clientSeen(int(options.get('connectivityTimeout',
                                  CONNECTIVITY_TIMEOUT_DEFAULT))):
                logger.info('Connectivity check failed, rolling back')
                for network in networksAdded:
                    # If the new added network was created on top of
                    # existing bond, we need to keep the bond on rollback
                    # flow, else we will break the new created bond.
                    delNetwork(network, force=True,
                               implicitBonding=networks[network].
                               get('bonding') in bondings)
                raise ConfigNetworkError(ne.ERR_LOST_CONNECTION,
                                         'connectivity check failed')
    except:
        configWriter.restoreBackups()
        raise


def setSafeNetworkConfig():
    """Declare current network configuration as 'safe'"""
    execCmd([constants.EXT_VDSM_STORE_NET_CONFIG])


def usage():
    print """Usage:
    ./configNetwork.py add Network <attributes> <options>
                       edit oldNetwork newNetwork <attributes> <options>
                       del Network <options>
                       setup Network [None|attributes] \
[++ Network [None|attributes] [++ ...]] [:: <options>]

                       attributes = [vlan=...] [bonding=...] [nics=<nic1>,...]
                       options = [Force=<True|False>] [bridged=<True|False>]...
    """


def _parseKwargs(args):
    import API

    kwargs = dict(arg.split('=', 1) for arg in args)
    API.Global.translateNetOptionsToNew(kwargs)

    return kwargs


def main():
    if len(sys.argv) <= 1:
        usage()
        raise ConfigNetworkError(ne.ERR_BAD_PARAMS, "No action specified")
    if sys.argv[1] == 'list':
        listNetworks()
        return
    if len(sys.argv) <= 2:
        usage()
        raise ConfigNetworkError(ne.ERR_BAD_PARAMS, "No action specified")
    if sys.argv[1] == 'add':
        bridge = sys.argv[2]
        kwargs = _parseKwargs(sys.argv[3:])
        if 'nics' in kwargs:
            kwargs['nics'] = kwargs['nics'].split(',')
        addNetwork(bridge, **kwargs)
    elif sys.argv[1] == 'del':
        bridge = sys.argv[2]
        kwargs = _parseKwargs(sys.argv[3:])
        if 'nics' in kwargs:
            kwargs['nics'] = kwargs['nics'].split(',')
        delNetwork(bridge, **kwargs)
    elif sys.argv[1] == 'edit':
        oldBridge = sys.argv[2]
        newBridge = sys.argv[3]
        kwargs = _parseKwargs(sys.argv[4:])
        if 'nics' in kwargs:
            kwargs['nics'] = kwargs['nics'].split(',')
        editNetwork(oldBridge, newBridge, **kwargs)
    elif sys.argv[1] == 'setup':
        batchCommands, options = utils.listSplit(sys.argv[2:], '::', 1)
        d = {}
        for batchCommand in utils.listSplit(batchCommands, '++'):
            d[batchCommand[0]] = _parseKwargs(batchCommand[1:]) or None
        setupNetworks(d, **_parseKwargs(options))
    elif sys.argv[1] == 'show':
        bridge = sys.argv[2]
        kwargs = _parseKwargs(sys.argv[3:])
        showNetwork(bridge, **kwargs)
    else:
        usage()
        raise ConfigNetworkError(ne.ERR_BAD_PARAMS, "Unknown action specified")

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    try:
        main()
    except ConfigNetworkError as e:
        traceback.print_exc()
        print e.message
        sys.exit(e.errCode)
    sys.exit(0)
