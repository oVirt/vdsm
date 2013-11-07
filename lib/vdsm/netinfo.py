#
# Copyright 2009-2013 Red Hat, Inc.
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

from collections import namedtuple
import errno
from glob import iglob
from itertools import chain
import logging
import os
import shlex
import socket
import struct
from xml.dom import minidom

import ethtool

from .config import config
from . import constants
from .ipwrapper import getLink, getLinks
from .ipwrapper import Route
from .ipwrapper import routeShowAllDefaultGateways
from . import libvirtconnection
from .ipwrapper import linkShowDev
from .utils import anyFnmatch
from .netconfpersistence import RunningConfig


NET_CONF_DIR = '/etc/sysconfig/network-scripts/'
# ifcfg persistence directories
NET_CONF_BACK_DIR = constants.P_VDSM_LIB + 'netconfback/'
NET_LOGICALNET_CONF_BACK_DIR = NET_CONF_BACK_DIR + 'logicalnetworks/'

NET_CONF_PREF = NET_CONF_DIR + 'ifcfg-'
PROC_NET_VLAN = '/proc/net/vlan/'
NET_PATH = '/sys/class/net'
BONDING_MASTERS = '/sys/class/net/bonding_masters'
BONDING_SLAVES = '/sys/class/net/%s/bonding/slaves'
BONDING_OPT = '/sys/class/net/%s/bonding/%s'
_BONDING_FAILOVER_MODES = frozenset(('1', '3'))
_BONDING_LOADBALANCE_MODES = frozenset(('0', '2', '4', '5', '6'))

LIBVIRT_NET_PREFIX = 'vdsm-'
DUMMY_BRIDGE = ';vdsmdummy;'
DEFAULT_MTU = '1500'

REQUIRED_BONDINGS = frozenset(('bond0', 'bond1', 'bond2', 'bond3', 'bond4'))

_Qos = namedtuple('Qos', 'inbound outbound')

OPERSTATE_UP = 'up'


def _nic_devices():
    """
    Returns an iterable of nic devices real or fakes, available on the host.
    """
    deviceLinks = getLinks()
    for device in deviceLinks:
        if device.isNIC() or device.isFakeNIC():
            yield device


def nics():
    """
    Returns a list of nics devices available to be used by vdsm.
    The list of nics is built by filtering out nics defined
    as hidden, fake or hidden bonds (with related nics'slaves).
    """
    def isEnslavedByHiddenBond(device):
        """
        Returns boolean stating if a nic device is enslaved to an hidden bond.
        """
        hidden_bonds = config.get('vars', 'hidden_bonds').split(',')
        valid_bonds_fn = (bond_fn for bond_fn in hidden_bonds if bond_fn)

        for bond_fn in valid_bonds_fn:
            for bond_path in iglob(NET_PATH + '/' + bond_fn):
                bond = os.path.basename(bond_path)
                if device in slaves(bond):
                    return True
        return False

    def isManagedByVdsm(device):
        """Returns boolean stating if a device should be managed by vdsm."""
        return (not device.isHidden() and
                not isEnslavedByHiddenBond(device.name))

    res = [dev.name for dev in _nic_devices() if isManagedByVdsm(dev)]
    return res


def bondings():
    """
    Returns list of available bonds managed by vdsm.
    """

    hidden_bonds = config.get('vars', 'hidden_bonds').split(',')
    res = []
    try:
        for bond in open(BONDING_MASTERS).readline().split():
            if not anyFnmatch(bond, hidden_bonds):
                res.append(bond)
    except IOError as e:
        if e.errno == os.errno.ENOENT:
            return res
        else:
            raise

    return res


def vlans():
    return [link.name for link in getLinks() if link.isVLAN() and
            not link.isHidden()]


def bridges():
    return [b.split('/')[-2] for b in iglob('/sys/class/net/*/bridge')
            if b.split('/')[-2] != DUMMY_BRIDGE]


def networks():
    """
    Get dict of networks from libvirt

    :returns: dict of networkname={properties}
    :rtype: dict of dict
            { 'ovirtmgmt': { 'bridge': 'ovirtmgmt', 'bridged': True,
                            'qosInbound': {'average': '1024', 'burst': '',
                                           'peak': ''},
                            'qosOutbound': {'average': '1024', 'burst': '2048',
                                           'peak': ''}},
              'red': { 'iface': 'red', 'bridged': False
                        'qosInbound': '', 'qosOutbound': ''} }
    """
    nets = {}
    conn = libvirtconnection.get()
    allNets = ((net, net.name()) for net in conn.listAllNetworks(0))
    for net, netname in allNets:
        if netname.startswith(LIBVIRT_NET_PREFIX):
            netname = netname[len(LIBVIRT_NET_PREFIX):]
            nets[netname] = {}
            xml = minidom.parseString(net.XMLDesc(0))
            qos = _parseBandwidthQos(xml)
            nets[netname]['qosInbound'] = qos.inbound
            nets[netname]['qosOutbound'] = qos.outbound
            interfaces = xml.getElementsByTagName('interface')
            if len(interfaces) > 0:
                nets[netname]['iface'] = interfaces[0].getAttribute('dev')
                nets[netname]['bridged'] = False
            else:
                nets[netname]['bridge'] = \
                    xml.getElementsByTagName('bridge')[0].getAttribute('name')
                nets[netname]['bridged'] = True
    return nets


def _parseBandwidthQos(networkXml):
    """
    Extract the Qos information
    :param networkXml: instance of xml.dom.minidom.Document
    :return: _Qos namedtuple containing inbound and outbound qos dicts.
    """

    qos = _Qos("", "")

    def extractQos(bandWidthElem, trafficType):
        qos = ""
        elem = bandWidthElem.getElementsByTagName(trafficType)
        if elem:
            qos = {'average': elem[0].getAttribute('average'),
                   'burst': elem[0].getAttribute('burst'),
                   'peak': elem[0].getAttribute('peak')}
        return qos

    bandwidthElem = networkXml.getElementsByTagName('bandwidth')
    if bandwidthElem:
        inbound = extractQos(bandwidthElem[0], "inbound")
        outbound = extractQos(bandwidthElem[0], "outbound")
        qos = _Qos(inbound, outbound)

    return qos


def slaves(bonding):
    return open(BONDING_SLAVES % bonding).readline().split()


def bondOpts(bond, keys=None):
    """ Returns a dictionary of bond option name and a values iterable. E.g.,
    {'mode': ('balance-rr', '0'), 'xmit_hash_policy': ('layer2', '0')}
    """
    if keys is None:
        paths = iglob(BONDING_OPT % (bond, '*'))
    else:
        paths = (BONDING_OPT % (bond, key) for key in keys)
    opts = {}
    for path in paths:
        with open(path) as optFile:
            opts[os.path.basename(path)] = optFile.read().rstrip().split(' ')
    return opts


def ports(bridge):
    return os.listdir('/sys/class/net/' + bridge + '/brif')


def getMtu(iface):
    mtu = open('/sys/class/net/%s/mtu' % iface).readline().rstrip()
    return int(mtu)


def getMaxMtu(devs, mtu):
    """
    Get the max MTU value from current state/parameter

    :param devs: iterable of network devices
    :type devs: iterable

    :param mtu: mtu value
    :type mtu: integer

    getMaxMtu return the highest value in a connection tree,
    it check if a vlan, bond that have a higher mtu value
    """
    return max([getMtu(dev) for dev in devs] + [mtu])


def bridge_stp_state(bridge):
    stp = file('/sys/class/net/%s/bridge/stp_state' % bridge).readline()
    if stp == '1\n':
        return 'on'
    else:
        return 'off'


def isvirtio(dev):
    return 'virtio' in os.readlink('/sys/class/net/%s/device' % dev)


def isbonding(dev):
    return os.path.exists('/sys/class/net/%s/bonding' % dev)


def operstate(dev):
    with open('/sys/class/net/%s/operstate' % dev) as operstateFile:
        return operstateFile.read().strip()


def speed(dev):
    # return the speed of devices that are capable of replying
    try:
        # operstat() filters out down/disabled nics
        if operstate(dev) != OPERSTATE_UP:
            return 0

        # nics() filters out OS devices (bonds, vlans, bridges)
        # virtio is a valid device, but doesn't support speed
        if dev in nics() and not isvirtio(dev):
            # the device may have been disabled/downed after checking
            # so we validate the return value as sysfs may return
            # special values to indicate the device is down/disabled
            with open('/sys/class/net/%s/speed' % dev) as speedFile:
                s = int(speedFile.read())
            if s not in (2 ** 16 - 1, 2 ** 32 - 1) or s > 0:
                return s
        elif dev in bondings():
            bondopts = bondOpts(dev, keys=['slaves', 'active_slave', 'mode'])
            if bondopts['slaves']:
                if bondopts['mode'][1] in _BONDING_FAILOVER_MODES:
                    s = speed(bondopts['active_slave'][0])
                elif bondopts['mode'][1] in _BONDING_LOADBALANCE_MODES:
                    s = sum(speed(slave) for slave in bondopts['slaves'])
                return s

    except Exception:
        logging.exception('cannot read %s speed', dev)
    return 0


def getaddr(dev):
    dev_info_list = ethtool.get_interfaces_info(dev.encode('utf8'))
    addr = dev_info_list[0].ipv4_address
    if addr is None:
        addr = ''
    return addr


def prefix2netmask(prefix):
    if not 0 <= prefix <= 32:
        raise ValueError('%s is not a valid prefix value. It must be between '
                         '0 and 32')
    return socket.inet_ntoa(
        struct.pack("!I", int('1' * prefix + '0' * (32 - prefix), 2)))


def getnetmask(dev):
    dev_info_list = ethtool.get_interfaces_info(dev.encode('utf8'))
    netmask = dev_info_list[0].ipv4_netmask
    if netmask == 0:
        return ''
    return prefix2netmask(netmask)


def getgateway(gateways, dev):
    return gateways.get(dev, '')


def getIpInfo(dev):
    devInfo = ethtool.get_interfaces_info(dev.encode('utf8'))[0]
    addr = devInfo.ipv4_address
    netmask = devInfo.ipv4_netmask
    ipv6addrs = devInfo.get_ipv6_addresses()

    return (addr if addr else '',
            prefix2netmask(netmask) if netmask else '',
            [addr6.address + '/' + str(addr6.netmask) for addr6 in ipv6addrs])


def getipv6addrs(dev):
    """Return a list of IPv6 addresses in the format of 'address/prefixlen'."""
    dev_info_list = ethtool.get_interfaces_info(dev.encode('utf8'))
    ipv6addrs = dev_info_list[0].get_ipv6_addresses()
    return [addr.address + '/' + str(addr.netmask) for addr in ipv6addrs]


def gethwaddr(dev):
    return file('/sys/class/net/%s/address' % dev).read().strip()


def graph():
    for bridge in bridges():
        print bridge
        for iface in ports(bridge):
            print '\t' + iface
            if iface in vlans():
                iface = getVlanDevice(iface)
            if iface in bondings():
                for slave in slaves(iface):
                    print '\t\t' + slave


def getVlanBondingNic(bridge):
    """Return the (vlan, bonding, nics) tupple that belongs to bridge."""

    if bridge not in bridges():
        raise ValueError('unknown bridge %s' % bridge)
    vlan = bonding = ''
    nics = []
    for iface in ports(bridge):
        if iface in vlans():
            iface = getVlanDevice(iface)
            vlan = getVlanID(iface)
        if iface in bondings():
            bonding = iface
            nics = slaves(iface)
        else:
            nics = [iface]
    return vlan, bonding, nics


def intToAddress(ip_num):
    "Convert an integer to the corresponding ip address in the dot-notation"
    ip_address = []

    for i in xrange(4):
        ip_num, ip_val = divmod(ip_num, 256)
        ip_address.append(str(ip_val))

    return '.'.join(ip_address)


def getRoutes():
    """Return the default gateway for each interface that has one."""
    default_routes = (Route.fromText(text) for text in
                      routeShowAllDefaultGateways())
    return dict((route.device, route.ipaddr) for route in default_routes)


def ipv6StrToAddress(ipv6_str):

    return socket.inet_ntop(
        socket.AF_INET6,
        struct.pack('>QQ', *divmod(int(ipv6_str, 16), 2 ** 64)))


def getIPv6Routes():
    """
    Return the default IPv6 gateway for each interface or None if not found.
    """

    ipv6gateways = dict()

    try:
        with open("/proc/net/ipv6_route") as route_file:
            for route_line in route_file.xreadlines():
                route_parm = route_line.rstrip().split(' ')
                dest = route_parm[0]
                prefix = route_parm[1]
                nexthop = route_parm[4]
                device = route_parm[-1]
                if dest == '0' * 32 and prefix == '00' and nexthop != '0' * 32:
                    ipv6gateways[device] = ipv6StrToAddress(nexthop)
    except IOError as e:
        if e.errno == errno.ENOENT:
            # ipv6 module not loaded
            pass
        else:
            raise

    return ipv6gateways


def getIfaceCfg(iface):
    ifaceCfg = {}
    try:
        with open(NET_CONF_PREF + iface) as f:
            ifaceCfg = dict(
                l.split('=', 1) for l in shlex.split(f, comments=True))
    except Exception:
        pass
    return ifaceCfg


def getBootProtocol(iface, persistence=None):
    if persistence is None:
        persistence = config.get('vars', 'persistence')

    if persistence == 'ifcfg':
        return getIfaceCfg(iface).get('BOOTPROTO')
    elif persistence == 'unified':
        runningConfig = RunningConfig()

        # If the network is bridged its iface name will be its network name
        network = runningConfig.networks.get(iface)
        if network is not None:
            return network.get('bootproto')

        # Otherwise we need to search if the iface is the device for a network
        for network, attributes in runningConfig.networks.iteritems():
            nic = attributes.get('nic')
            bonding = attributes.get('bonding')
            vlan = attributes.get('vlan')
            if iface in (nic, bonding,
                         "%s.%s" % (nic, vlan), "%s.%s" % (bonding, vlan)):
                return attributes.get('bootproto')

        return None
    else:
        raise NotImplementedError


def permAddr():
    paddr = {}
    for b in bondings():
        slave = ''
        for line in file('/proc/net/bonding/' + b):
            if line.startswith('Slave Interface: '):
                slave = line[len('Slave Interface: '):-1]
            if line.startswith('Permanent HW addr: '):
                addr = line[len('Permanent HW addr: '):-1]
                paddr[slave] = addr.upper()
    return paddr


def _getNetInfo(iface, bridged, gateways, ipv6routes, qosInbound, qosOutbound):
    '''Returns a dictionary of properties about the network's interface status.
    Raises a KeyError if the iface does not exist.'''
    data = {}
    try:
        if bridged:
            data.update({'ports': ports(iface), 'stp': bridge_stp_state(iface),
                         'cfg': getIfaceCfg(iface)})
        else:
            # ovirt-engine-3.1 expects to see the "interface" attribute iff the
            # network is bridgeless. Please remove the attribute and this
            # comment when the version is no longer supported.
            data['interface'] = iface

        ipv4addr, ipv4netmask, ipv6addrs = getIpInfo(iface)
        data.update({'iface': iface, 'bridged': bridged,
                     'addr': ipv4addr, 'netmask': ipv4netmask,
                     'gateway': getgateway(gateways, iface),
                     'ipv6addrs': ipv6addrs,
                     'ipv6gateway': ipv6routes.get(iface, '::'),
                     'mtu': str(getMtu(iface)),
                     'qosInbound': qosInbound,
                     'qosOutbound': qosOutbound})
    except (IOError, OSError) as e:
        if e.errno == errno.ENOENT:
            logging.info('Obtaining info for net %s.', iface, exc_info=True)
            raise KeyError('Network %s was not found' % iface)
        else:
            raise
    return data


def _bridgeinfo(bridge, gateways, ipv6routes):
    info = _devinfo(bridge)
    info.update({'gateway': getgateway(gateways, bridge),
                'ipv6gateway': ipv6routes.get(bridge, '::'),
                'ports': ports(bridge), 'stp': bridge_stp_state(bridge)})
    return (bridge, info)


def _nicinfo(nic, paddr):
    info = _devinfo(nic)
    info.update({'hwaddr': gethwaddr(nic), 'speed': speed(nic)})
    if paddr.get(nic):
        info['permhwaddr'] = paddr[nic]
    return (nic, info)


def _bondinfo(bond):
    info = _devinfo(bond)
    info.update({'hwaddr': gethwaddr(bond), 'slaves': slaves(bond)})
    return (bond, info)


def _vlaninfo(vlan):
    info = _devinfo(vlan)
    info.update({'iface': getVlanDevice(vlan), 'vlanid': getVlanID(vlan)})
    return (vlan, info)


def _devinfo(dev):
    ipv4addr, ipv4netmask, ipv6addrs = getIpInfo(dev)
    return {'addr': ipv4addr,
            'cfg': getIfaceCfg(dev),
            'ipv6addrs': ipv6addrs,
            'mtu': str(getMtu(dev)),
            'netmask': ipv4netmask}


def get():
    d = {}
    gateways = getRoutes()
    ipv6routes = getIPv6Routes()
    paddr = permAddr()
    d['networks'] = {}

    for net, netAttr in networks().iteritems():
        try:
            d['networks'][net] = _getNetInfo(netAttr.get('iface', net),
                                             netAttr['bridged'], gateways,
                                             ipv6routes,
                                             netAttr.get('qosInbound'),
                                             netAttr.get('qosOutbound'))
        except KeyError:
            continue  # Do not report missing libvirt networks.

    d['bridges'] = dict([_bridgeinfo(bridge, gateways, ipv6routes)
                         for bridge in bridges()])
    d['nics'] = dict([_nicinfo(nic, paddr) for nic in nics()])
    d['bondings'] = dict([_bondinfo(bond) for bond in bondings()])
    d['vlans'] = dict([_vlaninfo(vlan) for vlan in vlans()])
    return d


def isVlanned(dev):
    return any(vlan.startswith(dev + '.') for vlan in vlans())


def getVlanDevice(vlan):
    """ Return the device of the given VLAN. """
    out = linkShowDev(vlan)

    # out example:
    # 6: eth0.10@eth0: <BROADCAST,MULTICAST> mtu 1500...
    return str(out).split(':')[1].strip().split('@')[1]


def getVlanID(vlan):
    """ Return the ID of the given VLAN. """
    vlanLink = getLink(vlan)
    return int(vlanLink.vlanid)


def getIpAddresses():
    "Return a list of the host's IP addresses"
    return filter(None, [getaddr(i) for i in ethtool.get_active_devices()])


def IPv4toMapped(ip):
    """Return an IPv6 IPv4-mapped address for the IPv4 address"""
    mapped = None

    try:
        ipv6bin = '\x00' * 10 + '\xff\xff' + socket.inet_aton(ip)
        mapped = socket.inet_ntop(socket.AF_INET6, ipv6bin)
    except socket.error as e:
        logging.debug("getIfaceByIP: %s" % str(e))

    return mapped


def getIfaceByIP(ip):
    for info in ethtool.get_interfaces_info(ethtool.get_active_devices()):

        for ipv4addr in info.get_ipv4_addresses():
            if ip == ipv4addr.address or ip == IPv4toMapped(ipv4addr.address):
                return info.device

        for ipv6addr in info.get_ipv6_addresses():
            if ip == ipv6addr.address:
                return info.device

    return ''


class NetInfo(object):
    def __init__(self, _netinfo=None):
        if _netinfo is None:
            _netinfo = get()

        self.networks = _netinfo['networks']
        self.vlans = _netinfo['vlans']
        self.nics = _netinfo['nics']
        self.bondings = _netinfo['bondings']

    def getNetworksAndVlansForIface(self, iface):
        """ Returns tuples of (bridge/network, vlan) connected to  nic/bond """
        return chain(self.getBridgedNetworksAndVlansForIface(iface),
                     self.getBridgelessNetworksAndVlansForIface(iface))

    def getBridgedNetworksAndVlansForIface(self, iface):
        """ Returns tuples of (bridge, vlan) connected to nic/bond """
        for network, netdict in self.networks.iteritems():
            if netdict['bridged']:
                for interface in netdict['ports']:
                    if iface == interface:
                        yield (network, None)
                    elif interface.startswith(iface + '.'):
                        yield (network, interface.split('.', 1)[1])

    def getBridgelessNetworksAndVlansForIface(self, iface):
        """ Returns tuples of (network, vlan) connected to nic/bond """
        for network, netdict in self.networks.iteritems():
            if not netdict['bridged']:
                if iface == netdict['iface']:
                    yield (network, None)
                elif netdict['iface'].startswith(iface + '.'):
                    yield (network, getVlanID(netdict['iface']))

    def getVlansForIface(self, iface):
        for vlanDevName in self.getVlanDevsForIface(iface):
            yield getVlanID(vlanDevName)

    def getVlanDevsForIface(self, iface):
        for v, vdict in self.vlans.iteritems():
            if iface == vdict['iface']:
                yield v

    def getNetworkForIface(self, iface):
        """ Return the network attached to nic/bond """
        for network, netdict in self.networks.iteritems():
            if ('ports' in netdict and iface in netdict['ports'] or
                    iface == netdict['iface']):
                return network

    def getBridgelessNetworks(self):
        """ Return all bridgless networks."""
        for network, netdict in self.networks.iteritems():
            if not netdict['bridged']:
                yield network

    def getBridgelessNetworkForIface(self, iface):
        """ Return the bridgeless network attached to nic/bond """
        for network, netdict in self.networks.iteritems():
            if not netdict['bridged'] and iface == netdict['iface']:
                return network

    def getBridgedNetworkForIface(self, iface):
        """ Return all bridged networks attached to nic/bond """
        for bridge, netdict in self.networks.iteritems():
            if netdict['bridged'] and iface in netdict['ports']:
                return bridge

    def getBondingsForNic(self, nic):
        for b, bdict in self.bondings.iteritems():
            if nic in bdict['slaves']:
                yield b

    def getNicsForBonding(self, bond):
        bondAttrs = self.bondings[bond]
        return bondAttrs['slaves']

    def getBondingForNic(self, nic):
        bondings = list(self.getBondingsForNic(nic))
        if bondings:
            assert len(bondings) == 1, \
                "Unexpected configuration: More than one bonding per nic"
            return bondings[0]
        return None

    def getNicsVlanAndBondingForNetwork(self, network):
        vlan = None
        bonding = None
        lnics = []

        if self.networks[network]['bridged']:
            ports = self.networks[network]['ports']
        else:
            ports = []
            interface = self.networks[network]['iface']
            ports.append(interface)

        for port in ports:
            if port in self.vlans:
                assert vlan is None
                nic = getVlanDevice(port)
                vlan = getVlanID(port)
                assert self.vlans[port]['iface'] == nic
                port = nic
            if port in self.bondings:
                assert bonding is None
                bonding = port
                lnics += self.bondings[bonding]['slaves']
            elif port in self.nics:
                lnics.append(port)

        return lnics, vlan, bonding

    def ifaceUsers(self, iface):
        "Returns a list of entities using the interface"
        users = set()
        for n, ndict in self.networks.iteritems():
            if ndict['bridged'] and iface in ndict['ports']:
                users.add(n)
            elif not ndict['bridged'] and iface == ndict['iface']:
                users.add(n)
        for b, bdict in self.bondings.iteritems():
            if iface in bdict['slaves']:
                users.add(b)
        for v, vdict in self.vlans.iteritems():
            if iface == vdict['iface']:
                users.add(v)
        return users
