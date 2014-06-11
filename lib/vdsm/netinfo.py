#
# Copyright 2009-2014 Red Hat, Inc.
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

from collections import defaultdict, namedtuple
import errno
from glob import iglob
from datetime import datetime
from functools import partial
from itertools import chain
import logging
import os
import random
import shlex
import socket
import string
import struct
from xml.dom import minidom

import ethtool

from .config import config
from . import constants
from .ipwrapper import drv_name
from .ipwrapper import DUMMY_BRIDGE
from .ipwrapper import getLink, getLinks, Link
from .ipwrapper import IPRoute2Error
from .ipwrapper import Route
from .ipwrapper import routeGet
from .ipwrapper import routeShowGateways, routeShowAllDefaultGateways
from . import libvirtconnection
from .netconfpersistence import RunningConfig
from .netlink import iter_addrs, iter_links
from .utils import execCmd, memoized, CommandPath


NET_CONF_DIR = '/etc/sysconfig/network-scripts/'
# ifcfg persistence directories
NET_CONF_BACK_DIR = constants.P_VDSM_LIB + 'netconfback/'
NET_LOGICALNET_CONF_BACK_DIR = NET_CONF_BACK_DIR + 'logicalnetworks/'

# possible names of dhclient's lease files (e.g. as NetworkManager's slave)
_DHCLIENT_LEASES_GLOBS = [
    '/var/lib/dhclient/dhclient-*.lease',   # Fedora 20 legacy network service
    '/var/lib/dhclient/dhclient-*.leases',  # RHEL 6.5 network service
    '/var/lib/NetworkManager/dhclient-*.lease',  # NetworkManager
    '/var/lib/dhclient/dhclient.leases',  # default
]

NET_CONF_PREF = NET_CONF_DIR + 'ifcfg-'
PROC_NET_VLAN = '/proc/net/vlan/'
NET_PATH = '/sys/class/net'
BONDING_MASTERS = '/sys/class/net/bonding_masters'
BONDING_SLAVES = '/sys/class/net/%s/bonding/slaves'
BONDING_OPT = '/sys/class/net/%s/bonding/%s'
BRIDGING_OPT = '/sys/class/net/%s/bridge/%s'
_BONDING_FAILOVER_MODES = frozenset(('1', '3'))
_BONDING_LOADBALANCE_MODES = frozenset(('0', '2', '4', '5', '6'))
_EXCLUDED_BONDING_ENTRIES = frozenset((
    'slaves',  'active_slave', 'mii_status', 'queue_id', 'ad_aggregator',
    'ad_num_ports', 'ad_actor_key', 'ad_partner_key', 'ad_partner_mac'
))
_IFCFG_ZERO_SUFFIXED = frozenset(
    ('IPADDR0', 'GATEWAY0', 'PREFIX0', 'NETMASK0'))
_TEE_BINARY = CommandPath('tee', constants.EXT_TEE)

LIBVIRT_NET_PREFIX = 'vdsm-'
DEFAULT_MTU = '1500'

_Qos = namedtuple('Qos', 'inbound outbound')

OPERSTATE_UP = 'up'
DUMMY_BRIDGE  # Appease flake8 since dummy bridge should be exported from here


def _visible_devs(predicate):
    """Returns a list of visible (vdsm manageable) links for which the
    predicate is True"""
    return [dev.name for dev in getLinks() if predicate(dev) and
            not dev.isHidden()]


nics = partial(_visible_devs, Link.isNICLike)
bondings = partial(_visible_devs, Link.isBOND)
vlans = partial(_visible_devs, Link.isVLAN)
bridges = partial(_visible_devs, Link.isBRIDGE)


def networks():
    """
    Get dict of networks from libvirt

    :returns: dict of networkname={properties}
    :rtype: dict of dict
            { 'ovirtmgmt': { 'bridge': 'ovirtmgmt', 'bridged': True,
                            'qosInbound': {'average': 1024, 'peak': 4096},
                            'qosOutbound': {'average': 1024, 'burst': 2048}},
              'red': { 'iface': 'red', 'bridged': False
                       'qosOutbound': {'average': 1024, 'burst': 2048}}}
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
            if qos.inbound:
                nets[netname]['qosInbound'] = qos.inbound
            if qos.outbound:
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

    qos = _Qos({}, {})

    def extractQos(bandWidthElem, trafficType):
        qos = {}
        elem = bandWidthElem.getElementsByTagName(trafficType)
        if elem:
            qos['average'] = int(elem[0].getAttribute('average'))
            if elem[0].hasAttribute('burst'):  # libvirt XML optional field
                qos['burst'] = int(elem[0].getAttribute('burst'))
            if elem[0].hasAttribute('peak'):  # libvirt XML optional field
                qos['peak'] = int(elem[0].getAttribute('peak'))
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
            opts[os.path.basename(path)] = [
                el for el in optFile.read().rstrip().split(' ') if el]
    return opts


def _realBondOpts(bond):
    """
    Return a dictionary in the same format as bondOpts(). Exclude entries that
    are not bonding options, e.g. 'ad_num_ports' or 'slaves'.
    """
    return dict(((opt, val) for (opt, val) in bondOpts(bond).iteritems()
                 if opt not in _EXCLUDED_BONDING_ENTRIES))


def bridgeOpts(bridge, keys=None):
    """Returns a dictionary of bridge option name and value. E.g.,
    {'max_age': '2000', 'gc_timer': '332'}"""
    BR_KEY_BLACKLIST = ('flush',)
    if keys is None:
        paths = iglob(BRIDGING_OPT % (bridge, '*'))
    else:
        paths = (BRIDGING_OPT % (bridge, key) for key in keys)
    opts = {}
    for path in paths:
        key = os.path.basename(path)
        if key in BR_KEY_BLACKLIST:
            continue
        with open(path) as optFile:
            opts[key] = optFile.read().rstrip()
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


def vlanSpeed(vlanName):
    """Returns the vlan's underlying device speed."""
    vlanDevName = getVlanDevice(vlanName)
    vlanDev = getLink(vlanDevName)
    if vlanDev.isNIC():
        speed = nicSpeed(vlanDevName)
    elif vlanDev.isBOND():
        speed = bondSpeed(vlanDevName)
    else:
        speed = 0
    return speed


def _ibHackedSpeed(nicName):
    """If the nic is an InfiniBand device, return a speed of 10000 Mbps.

    This is only needed until the kernel reports ib*/speed, see
    https://bugzilla.redhat.com/show_bug.cgi?id=1101314
    """
    try:
        return 10000 if drv_name(nicName) == 'ib_ipoib' else 0
    except IOError:
        return 0


def nicSpeed(nicName):
    """Returns the nic speed if it is a legal value and nicName refers to a
    nic, 0 otherwise."""
    try:
        # if the device is not up we must report 0
        if operstate(nicName) != OPERSTATE_UP:
            return 0
        with open('/sys/class/net/%s/speed' % nicName) as speedFile:
            s = int(speedFile.read())
        # the device may have been disabled/downed after checking
        # so we validate the return value as sysfs may return
        # special values to indicate the device is down/disabled
        if s not in (2 ** 16 - 1, 2 ** 32 - 1) or s > 0:
            return s
    except IOError as ose:
        if ose.errno == errno.EINVAL:
            return _ibHackedSpeed(nicName)
        else:
            logging.exception('cannot read %s nic speed', nicName)
    except Exception:
        logging.exception('cannot read %s speed', nicName)
    return 0


def bondSpeed(bondName):
    """Returns the bond speed if bondName refers to a bond, 0 otherwise."""
    opts = bondOpts(bondName, keys=['slaves', 'active_slave', 'mode'])
    try:
        if opts['slaves']:
            if opts['mode'][1] in _BONDING_FAILOVER_MODES:
                active_slave = opts['active_slave']
                s = nicSpeed(active_slave[0]) if active_slave else 0
            elif opts['mode'][1] in _BONDING_LOADBALANCE_MODES:
                s = sum(nicSpeed(slave) for slave in opts['slaves'])
            return s
    except Exception:
        logging.exception('cannot read %s speed', bondName)
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


def getDefaultGateway():
    output = routeShowGateways('main')
    return Route.fromText(output[0]) if output else None


def getgateway(gateways, dev):
    return gateways.get(dev, '')


def getIpInfo(dev, ipaddrs):
    ipv4addr = ''
    ipv4netmask = ''
    ipv4addrs = []
    ipv6addrs = []
    for addr in ipaddrs[dev]:
        if addr['family'] == 'inet':
            ipv4addrs.append(addr['address'])
            if 'secondary' not in addr['flags']:
                ipv4addr, prefix = addr['address'].split('/')
                ipv4netmask = prefix2netmask(addr['prefixlen'])
        else:
            ipv6addrs.append(addr['address'])
    return ipv4addr, ipv4netmask, ipv4addrs, ipv6addrs


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
    return dict((route.device, route.via) for route in default_routes)


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
            for line in shlex.split(f, comments=True):
                k, v = line.split('=', 1)
                if k in _IFCFG_ZERO_SUFFIXED:
                    k = k[:-1]
                ifaceCfg[k] = v
    except Exception:
        pass
    return ifaceCfg


def getBootProtocol(iface, persistence=None):
    if persistence is None:
        persistence = config.get('vars', 'net_persistence')

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


def _randomIfaceName():
    MAX_LENGTH = 15
    CHARS = string.ascii_lowercase + string.ascii_uppercase + string.digits

    return ''.join(random.choice(CHARS) for _ in range(MAX_LENGTH))


@memoized
def _getAllDefaultBondingOptions():
    """
    Return default options per mode, in a dictionary of dictionaries. All keys
    are strings.
    """
    teeCmd = _TEE_BINARY.cmd
    MAX_MODE = 6

    bondName = _randomIfaceName()
    rc, _, err = execCmd([teeCmd, BONDING_MASTERS],
                         data='+' + bondName, sudo=True)
    if rc:
        raise RuntimeError('Creating a reference bond failed', '\n'.join(err))

    opts = {}
    try:
        defaultMode = bondOpts(bondName, keys=['mode'])['mode']

        # read default values for all modes
        for mode in range(0, MAX_MODE + 1):
            mode = str(mode)
            rc, _, err = execCmd([teeCmd, BONDING_OPT % (bondName, 'mode')],
                                 data=mode, sudo=True)

            # only read non-empty options
            opts[mode] = dict(((opt, val) for (opt, val) in
                               _realBondOpts(bondName).iteritems() if val))
            opts[mode]['mode'] = defaultMode

    finally:
        execCmd([teeCmd, BONDING_MASTERS], data='-' + bondName, sudo=True)

    return opts


@memoized
def _getDefaultBondingOptions(mode=None):
    """
    Return default options for the given mode. If it is None, return options
    for the default mode (usually '0').
    """
    defaults = _getAllDefaultBondingOptions()

    if mode is None:
        mode = defaults['0']['mode'][-1]

    return defaults[mode]


def _getBondingOptions(bond):
    """
    Return non-empty options differing from defaults, excluding not actual or
    not applicable options, e.g. 'ad_num_ports' or 'slaves'.
    """
    opts = _realBondOpts(bond)
    mode = opts['mode'][-1] if 'mode' in opts else None
    defaults = _getDefaultBondingOptions(mode)

    return dict(((opt, val[-1]) for (opt, val) in opts.iteritems()
                 if val and val != defaults.get(opt)))


def _bondOptsForIfcfg(opts):
    """
    Options having symbolic values, e.g. 'mode', are presented by sysfs in
    the order symbolic name, numeric value, e.g. 'balance-rr 0'.
    Choose the numeric value from a list given by bondOpts().
    """
    return ' '.join((opt + '=' + val for (opt, val)
                     in sorted(opts.iteritems())))


def _getNetInfo(iface, dhcp4, bridged, gateways, ipv6routes, ipaddrs,
                qosInbound, qosOutbound):
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

        ipv4addr, ipv4netmask, ipv4addrs, ipv6addrs = getIpInfo(iface, ipaddrs)
        data.update({'iface': iface, 'bridged': bridged,
                     'addr': ipv4addr, 'netmask': ipv4netmask,
                     'bootproto4': 'dhcp' if iface in dhcp4 else 'none',
                     'gateway': getgateway(gateways, iface),
                     'ipv4addrs': ipv4addrs,
                     'ipv6addrs': ipv6addrs,
                     'ipv6gateway': ipv6routes.get(iface, '::'),
                     'mtu': str(getMtu(iface))})
        if qosInbound:
            data['qosInbound'] = qosInbound
        if qosOutbound:
            data['qosOutbound'] = qosOutbound
    except (IOError, OSError) as e:
        if e.errno == errno.ENOENT:
            logging.info('Obtaining info for net %s.', iface, exc_info=True)
            raise KeyError('Network %s was not found' % iface)
        else:
            raise
    return data


def _bridgeinfo(link, gateways, ipv6routes):
    return {'gateway': getgateway(gateways, link.name),
            'ipv6gateway': ipv6routes.get(link.name, '::'),
            'ports': ports(link.name),
            'stp': bridge_stp_state(link.name),
            'opts': bridgeOpts(link.name)}


def _nicinfo(link, paddr):
    info = {'hwaddr': link.address, 'speed': nicSpeed(link.name)}
    if paddr.get(link.name):
        info['permhwaddr'] = paddr[link.name]
    return info


def _bondinfo(link):
    return {'hwaddr': link.address, 'slaves': slaves(link.name),
            'opts': _getBondingOptions(link.name)}


def _bondOptsCompat(info):
    """Add legacy ifcfg option if missing."""
    if info['opts'] and 'BONDING_OPTS' not in info['cfg']:
        info['cfg']['BONDING_OPTS'] = _bondOptsForIfcfg(info['opts'])


def _vlaninfo(link):
    return {'iface': link.device, 'vlanid': link.vlanid}


def _devinfo(link, ipaddrs, dhcp4):
    ipv4addr, ipv4netmask, ipv4addrs, ipv6addrs = getIpInfo(link.name, ipaddrs)
    info = {'addr': ipv4addr,
            'cfg': getIfaceCfg(link.name),
            'ipv4addrs': ipv4addrs,
            'ipv6addrs': ipv6addrs,
            'bootproto4': 'dhcp' if link.name in dhcp4 else 'none',
            'mtu': str(link.mtu),
            'netmask': ipv4netmask}
    if 'BOOTPROTO' not in info['cfg']:
        info['cfg']['BOOTPROTO'] = info['bootproto4']
    return info


def _parseExpiryTime(expiryTime):
    EPOCH = 'epoch '

    if expiryTime.startswith(EPOCH):
        since_epoch = expiryTime[len(EPOCH):]
        return datetime.utcfromtimestamp(float(since_epoch))

    else:
        return datetime.strptime(expiryTime, '%w %Y/%m/%d %H:%M:%S')


def _parseLeaseFile(leaseFile, ipv6):
    LEASE = 'lease{0} {{\n'.format('6' if ipv6 else '')
    IFACE = '  interface "'
    IFACE_END = '";\n'
    EXPIRE = '  expire '

    interfaces = set()
    insideLease = False

    for line in leaseFile:
        if insideLease:
            if line.startswith(IFACE) and line.endswith(IFACE_END):
                name = line[len(IFACE):-len(IFACE_END)]

            elif line.startswith(EXPIRE):
                end = line.find(';')
                if end == -1:
                    continue  # the line should always contain a ;

                expiryTime = _parseExpiryTime(line[len(EXPIRE):end])
                if datetime.utcnow() > expiryTime:
                    insideLease = False
                    continue

            elif line == '}\n':
                insideLease = False
                if name:
                    interfaces.add(name)

        elif line == LEASE:
            insideLease = True
            name = ''

    return interfaces


def getDhclientIfaces(leaseFilesGlobs, ipv6=False):
    """Returns a set of interfaces configured using dhclient.

    dhclient stores DHCP leases to file(s) whose names can be specified
    by the leaseFilesGlobs parameter (an iterable of glob strings).

        TODO: dhclient6 does not use an 'expire' line, create a test to see
        if a line reading 'released;' is an unambiguous sign of an invalid
        DHCPv6 lease.

    To discover DHCPv6 leases set the ipv6 parameter to True."""

    interfaces = set()

    for leaseFilesGlob in leaseFilesGlobs:
        for leaseFile in iglob(leaseFilesGlob):
            with open(leaseFile) as leaseFile:
                interfaces.update(_parseLeaseFile(leaseFile, ipv6))

    return interfaces


def _getIpAddrs():
    addrs = defaultdict(list)
    for addr in iter_addrs():
        addrs[addr['label']].append(addr)
    return addrs


def _libvirtNets2vdsm(nets, dhcp4=None, gateways=None, ipv6routes=None,
                      ipAddrs=None):
    if dhcp4 is None:
        dhcp4 = getDhclientIfaces(_DHCLIENT_LEASES_GLOBS)
    if gateways is None:
        gateways = getRoutes()
    if ipv6routes is None:
        ipv6routes = getIPv6Routes()
    if ipAddrs is None:
        ipAddrs = _getIpAddrs()
    d = {}
    for net, netAttr in nets.iteritems():
        try:
            d[net] = _getNetInfo(netAttr.get('iface', net),
                                 dhcp4,
                                 netAttr['bridged'], gateways,
                                 ipv6routes, ipAddrs,
                                 netAttr.get('qosInbound'),
                                 netAttr.get('qosOutbound'))
        except KeyError:
            continue  # Do not report missing libvirt networks.
    return d


def _cfgBootprotoCompat(networks):
    """Set network 'cfg' 'BOOTPROTO' for backwards engine compatibility."""
    for netAttrs in networks.itervalues():
        if netAttrs['bridged'] and 'BOOTPROTO' not in netAttrs['cfg']:
            netAttrs['cfg']['BOOTPROTO'] = netAttrs['bootproto4']


def get(vdsmnets=None):
    d = {'bondings': {}, 'bridges': {}, 'networks': {}, 'nics': {},
         'vlans': {}}
    gateways = getRoutes()
    ipv6routes = getIPv6Routes()
    paddr = permAddr()
    ipaddrs = _getIpAddrs()
    dhcp4 = getDhclientIfaces(_DHCLIENT_LEASES_GLOBS)

    if vdsmnets is None:
        nets = networks()
        d['networks'] = _libvirtNets2vdsm(nets, dhcp4, gateways, ipv6routes,
                                          ipaddrs)
    else:
        d['networks'] = vdsmnets

    for dev in (link for link in getLinks() if not link.isHidden()):
        if dev.isBRIDGE():
            devinfo = d['bridges'][dev.name] = _bridgeinfo(dev, gateways,
                                                           ipv6routes)
        elif dev.isNICLike():
            devinfo = d['nics'][dev.name] = _nicinfo(dev, paddr)
        elif dev.isBOND():
            devinfo = d['bondings'][dev.name] = _bondinfo(dev)
        elif dev.isVLAN():
            devinfo = d['vlans'][dev.name] = _vlaninfo(dev)
        else:
            continue
        devinfo.update(_devinfo(dev, ipaddrs, dhcp4))
        if dev.isBOND():
            _bondOptsCompat(devinfo)

    _cfgBootprotoCompat(d['networks'])

    return d


def isVlanned(dev):
    return any(vlan.startswith(dev + '.') for vlan in vlans())


def getVlanDevice(vlan):
    """ Return the device of the given VLAN. """
    vlanLink = getLink(vlan)
    return vlanLink.device


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
        logging.debug("getIfaceByIP: %s", e)

    return mapped


def getRouteDeviceTo(destinationIP):
    """Return the name of the device leading to destinationIP or the empty
       string if none is found"""
    try:
        route = routeGet([destinationIP])[0]
    except (IPRoute2Error, IndexError):
        logging.exception('Could not route to %s', destinationIP)
        return ''

    try:
        return Route.fromText(route).device
    except ValueError:
        logging.exception('Could not parse route %s', route)
        return ''


def getDeviceByIP(ip):
    """
    Get network device by IP address
    :param ip: String representing IPv4 or IPv6, but not link-local IPv6
    """
    for info in ethtool.get_interfaces_info(ethtool.get_active_devices()):
        for ipv4addr in info.get_ipv4_addresses():
            if ip in (ipv4addr.address, IPv4toMapped(ipv4addr.address)):
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
        self.bridges = _netinfo['bridges']

    def updateDevices(self):
        """Updates the object device information while keeping the cached
        network information."""
        _netinfo = get(vdsmnets=self.networks)
        self.networks = _netinfo['networks']
        self.vlans = _netinfo['vlans']
        self.nics = _netinfo['nics']
        self.bondings = _netinfo['bondings']
        self.bridges = _netinfo['bridges']

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


def ifaceUsed(iface):
    """Lightweight implementation of bool(Netinfo.ifaceUsers()) that does not
    require a NetInfo object."""
    if os.path.exists(os.path.join(NET_PATH, iface, 'brport')):  # Is it a port
        return True
    for linkDict in iter_links():
        if linkDict['name'] == iface and 'master' in linkDict:  # Is it a slave
            return True
        if linkDict.get('device') == iface:  # Does it back a vlan
            return True
    for name, info in networks().iteritems():
        if info.get('iface') == iface:
            return True
    return False


def vlanDevsForIface(iface):
    for linkDict in iter_links():
        if linkDict.get('device') == iface:
            yield linkDict['name']
