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
import traceback
import time
import logging

from vdsm import constants
from vdsm import utils
from storage.misc import execCmd
import neterrors as ne
from neterrors import ConfigNetworkError
from vdsm import define
from vdsm import netinfo
from netconf.ifcfg import ConfigWriter
from netconf.ifcfg import Ifcfg
from netmodels import Bond
from netmodels import Bridge
from netmodels import IPv4
from netmodels import IpConfig
from netmodels import Nic
from netmodels import Vlan

CONNECTIVITY_TIMEOUT_DEFAULT = 4


def objectivizeNetwork(bridge=None, vlan=None, bonding=None,
                       bondingOptions=None, nics=None, mtu=None, ipaddr=None,
                       netmask=None, gateway=None, bootproto=None,
                       _netinfo=None, configurator=None, blockingdhcp=None,
                       implicitBonding=None, **opts):
    """
    Constructs an object hierarchy that describes the network configuration
    that is passed in the parameters.

    :param bridge: name of the bridge.
    :param vlan: vlan tag id.
    :param bonding: name of the bond.
    :param bondingOptions: bonding options separated by spaces.
    :param nics: list of nic names.
    :param mtu: the desired network maximum transmission unit.
    :param ipaddr: IPv4 address in dotted decimal format.
    :param netmask: IPv4 mask in dotted decimal format.
    :param gateway: IPv4 address in dotted decimal format.
    :param bootproto: protocol for getting IP config for the net, e.g., 'dhcp'
    :param _netinfo: network information snapshot.
    :param configurator: instance to use to apply the network configuration.
    :param blockingdhcp: whether to acquire dhcp IP config in a synced manner.
    :param implicitBonding: whether the bond's existance is tied to it's
                            master's.

    :returns: the top object of the hierarchy.
    """
    if configurator is None:
        configurator = Ifcfg()
    if _netinfo is None:
        _netinfo = netinfo.NetInfo()
    if bondingOptions and not bonding:
        raise ConfigNetworkError(ne.ERR_BAD_BONDING, 'Bonding options '
                                 'specified without bonding')
    topNetDev = None
    if bonding:
        topNetDev = Bond.objectivize(bonding, configurator, bondingOptions,
                                     nics, mtu, _netinfo, implicitBonding)
    elif nics:
        try:
            nic, = nics
        except ValueError:
            raise ConfigNetworkError(ne.ERR_BAD_BONDING, 'Multiple nics '
                                     'require a bonding device')
        else:
            bond = _netinfo.getBondingForNic(nic)
            if bond:
                raise ConfigNetworkError(ne.ERR_USED_NIC, 'nic %s already '
                                         'enslaved to %s' % (nic, bond))
            topNetDev = Nic(nic, configurator, mtu=mtu, _netinfo=_netinfo)
    if vlan:
        topNetDev = Vlan(topNetDev, vlan, configurator, mtu=mtu)
    if bridge:
        topNetDev = Bridge(bridge, configurator, port=topNetDev, mtu=mtu,
                           stp=opts.get('stp'),
                           forwardDelay=int(opts.get('forward_delay', 0)))
    if topNetDev is None:
        raise ConfigNetworkError(ne.ERR_BAD_PARAMS, 'Network defined without'
                                 'devices.')
    topNetDev.ip = IpConfig(inet=IPv4(ipaddr, netmask, gateway),
                            bootproto=bootproto,
                            blocking=utils.tobool(blockingdhcp))
    return topNetDev


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


def addNetwork(network, vlan=None, bonding=None, nics=None, ipaddr=None,
               netmask=None, prefix=None, mtu=None, gateway=None, force=False,
               configurator=None, bondingOptions=None, bridged=True,
               _netinfo=None, qosInbound=None, qosOutbound=None, **options):
    nics = nics or ()
    if _netinfo is None:
        _netinfo = netinfo.NetInfo()
    bridged = utils.tobool(bridged)

    if mtu:
        mtu = int(mtu)

    if prefix:
        if netmask:
            raise ConfigNetworkError(ne.ERR_BAD_PARAMS,
                                     'Both PREFIX and NETMASK supplied')
        else:
            try:
                netmask = netinfo.prefix2netmask(int(prefix))
            except ValueError as ve:
                raise ConfigNetworkError(ne.ERR_BAD_ADDR, "Bad prefix: %s" %
                                         ve)

    if not utils.tobool(force):
        logging.debug('validating network...')
        if network in _netinfo.networks:
            raise ConfigNetworkError(ne.ERR_USED_BRIDGE,
                                     'Network already exists')
        if bonding:
            _validateInterNetworkCompatibility(_netinfo, vlan, bonding,
                                               bridged)
        else:
            for nic in nics:
                _validateInterNetworkCompatibility(_netinfo, vlan, nic,
                                                   bridged)
    logging.info("Adding network %s with vlan=%s, bonding=%s, nics=%s,"
                 " bondingOptions=%s, mtu=%s, bridged=%s, options=%s",
                 network, vlan, bonding, nics, bondingOptions,
                 mtu, bridged, options)

    if configurator is None:
        configurator = Ifcfg()

    bootproto = options.pop('bootproto', None)

    netEnt = objectivizeNetwork(network if bridged else None, vlan, bonding,
                                bondingOptions, nics, mtu, ipaddr, netmask,
                                gateway, bootproto, _netinfo, configurator,
                                **options)

    # libvirt net addition must be done before creation so that on dhcp ifup
    # the dhcp hook will already see the network as belonging to vdsm.
    configurator.configureLibvirtNetwork(network, netEnt,
                                         qosInbound=qosInbound,
                                         qosOutbound=qosOutbound)
    netEnt.configure(**options)


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


def _delBrokenNetwork(network, netAttr, configurator):
    '''Adapts the network information of broken networks so that they can be
    deleted via delNetwork.'''
    _netinfo = netinfo.NetInfo()
    _netinfo.networks[network] = netAttr
    if _netinfo.networks[network]['bridged']:
        _netinfo.networks[network]['ports'] = ConfigWriter.ifcfgPorts(network)
    delNetwork(network, configurator=configurator, force=True,
               implicitBonding=False, _netinfo=_netinfo)


def _validateDelNetwork(network, vlan, bonding, nics, bridged,  _netinfo):
    if bonding:
        if set(nics) != set(_netinfo.bondings[bonding]["slaves"]):
            raise ConfigNetworkError(ne.ERR_BAD_NIC, 'delNetwork: %s are '
                                     'not all nics enslaved to %s' %
                                     (nics, bonding))
    if bridged:
        assertBridgeClean(network, vlan, bonding, nics)


def _delNonVdsmNetwork(network, vlan, bonding, nics, _netinfo, configurator):
    if network in netinfo.bridges():
        netEnt = objectivizeNetwork(bridge=network, vlan=vlan, bonding=bonding,
                                    nics=nics, _netinfo=_netinfo,
                                    configurator=configurator,
                                    implicitBonding=False)
        netEnt.remove()
    else:
        raise ConfigNetworkError(ne.ERR_BAD_BRIDGE, "Cannot delete network"
                                 " %r: It doesn't exist in the system" %
                                 network)


def delNetwork(network, vlan=None, bonding=None, nics=None, force=False,
               configurator=None, implicitBonding=True, _netinfo=None,
               **options):
    if _netinfo is None:
        _netinfo = netinfo.NetInfo()

    if configurator is None:
        configurator = Ifcfg()

    if network not in _netinfo.networks:
        logging.info("Network %r: doesn't exist in libvirt database", network)
        _delNonVdsmNetwork(network, vlan, bonding, nics, _netinfo,
                           configurator)
        return

    nics, vlan, bonding = _netinfo.getNicsVlanAndBondingForNetwork(network)
    bridged = _netinfo.networks[network]['bridged']

    logging.info("Removing network %s with vlan=%s, bonding=%s, nics=%s,"
                 "options=%s" % (network, vlan, bonding, nics, options))

    if not utils.tobool(force):
        _validateDelNetwork(network, vlan, bonding, nics, bridged, _netinfo)

    netEnt = objectivizeNetwork(bridge=network if bridged else None, vlan=vlan,
                                bonding=bonding, nics=nics, _netinfo=_netinfo,
                                configurator=configurator,
                                implicitBonding=implicitBonding)
    netEnt.ip.bootproto = netinfo.getBootProtocol(netEnt.name)
    netEnt.remove()
    # libvirt net removal must be done after removal so that on dhcp ifdown
    # the dhcp hook still sees the network as belonging to vdsm.
    configurator.removeLibvirtNetwork(network)

    # We need to gather NetInfo again to refresh networks info from libvirt.
    # The deleted bridge should never be up at this stage.
    _netinfo = netinfo.NetInfo()
    if network in _netinfo.networks:
        raise ConfigNetworkError(ne.ERR_USED_BRIDGE, 'delNetwork: bridge %s '
                                 'still exists' % network)


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
    configurator = Ifcfg()
    try:
        delNetwork(oldBridge, configurator=configurator, **options)
        addNetwork(newBridge, vlan=vlan, bonding=bonding, nics=nics,
                   configurator=configurator, **options)
    except:
        configurator.rollback()
        raise
    if utils.tobool(options.get('connectivityCheck', False)):
        if not clientSeen(int(options.get('connectivityTimeout',
                                          CONNECTIVITY_TIMEOUT_DEFAULT))):
            delNetwork(newBridge, force=True)
            configurator.rollback()
            return define.errCode['noConPeer']['status']['code']


def _validateNetworkSetup(networks, bondings):
    _netinfo = netinfo.NetInfo()

    for network, networkAttrs in networks.iteritems():
        if networkAttrs.get('remove', False):
            if set(networkAttrs) - set(['remove']):
                raise ConfigNetworkError(ne.ERR_BAD_PARAMS, 'Cannot specify '
                                         'any attribute when removing')

    for bonding, bondingAttrs in bondings.iteritems():
        Bond.validateName(bonding)
        if 'options' in bondingAttrs:
            Bond.validateOptions(bonding, bondingAttrs['options'])

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


def _handleBondings(bondings, configurator):
    """ Add/Edit/Remove bond interface """
    logger = logging.getLogger("_handleBondings")

    _netinfo = netinfo.NetInfo()

    for bondName, bondAttrs in bondings.items():
        bond = Bond.objectivize(bondName, configurator,
                                bondAttrs.get('options'),
                                bondAttrs.get('nics'), mtu=None,
                                _netinfo=_netinfo,
                                destroyOnMasterRemoval='remove' in bondAttrs)
        if 'remove' in bondAttrs:
            logger.debug("Removing bond %s with attributes %s", bondName,
                         bondAttrs)
            configurator.removeBond(bond)
            del bondings[bondName]
            del _netinfo.bondings[bondName]
        elif bondName in _netinfo.bondings:
            logger.debug("Editing bond %s with attributes %s", bondName,
                         bondAttrs)
            configurator.editBonding(bond, _netinfo)
        else:
            logger.debug("Creating bond %s with attributes %s", bondName,
                         bondAttrs)
            configurator.configureBond(bond)


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


def setupNetworks(networks, bondings, **options):
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
    configurator = Ifcfg()
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
                delNetwork(network, configurator=configurator, force=force,
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
                                  configurator=configurator)
                if 'remove' in networkAttrs:
                    del networks[network]
                    del libvirt_nets[network]
            else:
                networksAdded.add(network)

        _handleBondings(bondings, configurator)

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
            addNetwork(network, configurator=configurator,
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
        configurator.rollback()
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
