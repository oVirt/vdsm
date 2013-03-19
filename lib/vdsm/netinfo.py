#
# Copyright 2009-2011 Red Hat, Inc.
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

import os
import errno
import glob
import ethtool
import shlex
import logging
import socket
import struct
from fnmatch import fnmatch
from xml.dom import minidom
from itertools import chain

import libvirtconnection

import constants
from config import config

NET_CONF_DIR = '/etc/sysconfig/network-scripts/'
NET_CONF_BACK_DIR = constants.P_VDSM_LIB + 'netconfback/'
NET_LOGICALNET_CONF_BACK_DIR = NET_CONF_BACK_DIR + 'logicalnetworks/'

NET_CONF_PREF = NET_CONF_DIR + 'ifcfg-'
PROC_NET_VLAN = '/proc/net/vlan/'

LIBVIRT_NET_PREFIX = 'vdsm-'
DUMMY_BRIDGE = ';vdsmdummy;'


def _match_nic_name(nic, patterns):
    return any(map(lambda p: fnmatch(nic, p), patterns))


def nics():
    res = []
    hidden_nics = config.get('vars', 'hidden_nics').split(',')
    fake_nics = config.get('vars', 'fake_nics').split(',')

    for b in glob.glob('/sys/class/net/*'):
        nic = b.split('/')[-1]
        if not os.path.exists(os.path.join(b, 'device')):
            if _match_nic_name(nic, fake_nics):
                res.append(nic)
        elif not _match_nic_name(nic, hidden_nics):
            res.append(nic)

    return res


def bondings():
    return [b.split('/')[-2] for b in glob.glob('/sys/class/net/*/bonding')]


def vlans():
    return [b.split('/')[-1] for b in glob.glob('/sys/class/net/*.*')]


def bridges():
    return [b.split('/')[-2] for b in glob.glob('/sys/class/net/*/bridge')
            if b.split('/')[-2] != DUMMY_BRIDGE]


def networks():
    """
    Get dict of networks from libvirt

    :returns: dict of networkname={properties}
    :rtype: dict of dict
            { 'ovirtmgmt': { 'bridge': 'ovirtmgmt', 'bridged': True },
              'red': { 'iface': 'red', 'bridged': False } }
    """
    nets = {}
    conn = libvirtconnection.get()
    for name in conn.listNetworks():
        if name.startswith(LIBVIRT_NET_PREFIX):
            # remove the LIBVIRT_NET_PREFIX from the network name
            netname = name[len(LIBVIRT_NET_PREFIX):]
            nets[netname] = {}
            net = conn.networkLookupByName(name)
            xml = minidom.parseString(net.XMLDesc(0))
            interfaces = xml.getElementsByTagName('interface')
            if len(interfaces) > 0:
                nets[netname]['iface'] = interfaces[0].getAttribute('dev')
                nets[netname]['bridged'] = False
            else:
                nets[netname]['bridge'] = \
                    xml.getElementsByTagName('bridge')[0].getAttribute('name')
                nets[netname]['bridged'] = True
    return nets


def slaves(bonding):
    return [b.split('/')[-1].split('_', 1)[-1] for b in
            glob.glob('/sys/class/net/' + bonding + '/slave_*')]


def ports(bridge):
    return os.listdir('/sys/class/net/' + bridge + '/brif')


def getMtu(iface):
    mtu = file('/sys/class/net/%s/mtu' % iface).readline().rstrip()
    return mtu


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
    return file('/sys/class/net/%s/operstate' % dev).read().strip()


def speed(dev):
    # return the speed of devices that are capable of replying
    try:
        # nics() filters out OS devices (bonds, vlans, bridges)
        # operstat() filters out down/disabled nics
        # virtio is a valid device, but doesn't support speed
        if dev in nics() and operstate(dev) == 'up' and not isvirtio(dev):
            # the device may have been disabled/downed after checking
            # so we validate the return value as sysfs may return
            # special values to indicate the device is down/disabled
            s = int(file('/sys/class/net/%s/speed' % dev).read())
            if s not in (2 ** 16 - 1, 2 ** 32 - 1) or s > 0:
                return s
    except:
        logging.error('cannot read %s speed', dev, exc_info=True)
    return 0


def getaddr(dev):
    dev_info_list = ethtool.get_interfaces_info(dev.encode('utf8'))
    addr = dev_info_list[0].ipv4_address
    if addr is None:
        addr = ''
    return addr


def prefix2netmask(prefix):
    return socket.inet_ntoa(
        struct.pack("!I", int('1' * prefix + '0' * (32 - prefix), 2)))


def getnetmask(dev):
    dev_info_list = ethtool.get_interfaces_info(dev.encode('utf8'))
    netmask = dev_info_list[0].ipv4_netmask
    if netmask == 0:
        return ''
    return prefix2netmask(netmask)


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
                iface = iface.split('.')[0]
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
            iface, vlan = iface.split('.')
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
    """Return the interface default gateway or None if not found."""

    gateways = dict()

    with open("/proc/net/route") as route_file:
        route_file.readline()  # skip header line

        for route_line in route_file.xreadlines():
            route_parm = route_line.rstrip().split('\t')

            if route_parm[1] == '00000000' and route_parm[2] != '00000000':
                ip_num = int(route_parm[2], 16)
                gateways[route_parm[0]] = intToAddress(ip_num)

    return gateways


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
    d = {}
    try:
        for line in open(NET_CONF_PREF + iface).readlines():
            line = line.strip()
            if line.startswith('#'):
                continue
            try:
                k, v = line.split('=', 1)
                d[k] = ''.join(shlex.split(v))
            except:
                pass
    except:
        pass
    return d


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


def _getNetInfo(iface, bridged, routes, ipv6routes):
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
        data.update({'iface': iface, 'bridged': bridged,
                     'addr': getaddr(iface), 'netmask': getnetmask(iface),
                     'gateway': routes.get(iface, '0.0.0.0'),
                     'ipv6addrs': getipv6addrs(iface),
                     'ipv6gateway': ipv6routes.get(iface, '::'),
                     'mtu': getMtu(iface)})
    except OSError as e:
        if e.errno == errno.ENOENT:
            logging.info('Obtaining info for net %s.', iface, exc_info=True)
            raise KeyError('Network %s was not found' % iface)
        else:
            raise
    return data


def get():
    d = {}
    routes = getRoutes()
    ipv6routes = getIPv6Routes()
    d['networks'] = {}

    for net, netAttr in networks().iteritems():
        try:
            d['networks'][net] = _getNetInfo(netAttr.get('iface', net),
                                             netAttr['bridged'], routes,
                                             ipv6routes)
        except KeyError:
            continue  # Do not report missing libvirt networks.

    d['bridges'] = dict([(bridge, {'ports': ports(bridge),
                                   'stp': bridge_stp_state(bridge),
                                   'addr': getaddr(bridge),
                                   'netmask': getnetmask(bridge),
                                   'gateway': routes.get(bridge, '0.0.0.0'),
                                   'ipv6addrs': getipv6addrs(bridge),
                                   'ipv6gateway': ipv6routes.get(bridge, '::'),
                                   'mtu': getMtu(bridge),
                                   'cfg': getIfaceCfg(bridge),
                                   })
                         for bridge in bridges()])

    d['nics'] = dict([(nic, {'speed': speed(nic),
                             'addr': getaddr(nic),
                             'netmask': getnetmask(nic),
                             'ipv6addrs': getipv6addrs(nic),
                             'hwaddr': gethwaddr(nic),
                             'mtu': getMtu(nic),
                             'cfg': getIfaceCfg(nic),
                             })
                      for nic in nics()])
    paddr = permAddr()
    for nic, nd in d['nics'].iteritems():
        if paddr.get(nic):
            nd['permhwaddr'] = paddr[nic]
    d['bondings'] = dict([(bond, {'slaves': slaves(bond),
                                  'addr': getaddr(bond),
                                  'netmask': getnetmask(bond),
                                  'ipv6addrs': getipv6addrs(bond),
                                  'hwaddr': gethwaddr(bond),
                                  'cfg': getIfaceCfg(bond),
                                  'mtu': getMtu(bond)})
                          for bond in bondings()])
    d['vlans'] = dict([(vlan, {'iface': vlan.split('.')[0],
                               'addr': getaddr(vlan),
                               'netmask': getnetmask(vlan),
                               'ipv6addrs': getipv6addrs(vlan),
                               'mtu': getMtu(vlan),
                               'cfg': getIfaceCfg(vlan),
                               })
                       for vlan in vlans()])
    return d


def getVlanDevice(vlan):
    """ Return the device of the given VLAN. """
    dev = None

    if os.path.exists(PROC_NET_VLAN + vlan):
        for line in file(PROC_NET_VLAN + vlan).readlines():
            if "Device:" in line:
                dummy, dev = line.split()
                break

    return dev


def getVlanID(vlan):
    """ Return the ID of the given VLAN. """
    id = None

    if os.path.exists(PROC_NET_VLAN):
        for line in file(PROC_NET_VLAN + vlan).readlines():
            if "VID" in line:
                id = line.split()[2]
                break

    return id


def getIpAddresses():
    "Return a list of the host's IP addresses"
    return filter(None, [getaddr(i) for i in ethtool.get_active_devices()])


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
                    yield (network, netdict['iface'].split('.', 1)[1])

    def getVlansForIface(self, iface):
        for v, vdict in self.vlans.iteritems():
            if iface == vdict['iface']:
                yield v.split('.', 1)[1]

    def getNetworksForIface(self, iface):
        """ Return all networks attached to nic/bond """
        return chain(self.getBridgelessNetworksForIface(iface),
                     self.getBridgedNetworksForIface(iface))

    def getBridgelessNetworks(self):
        """ Return all bridgless networks."""
        for network, netdict in self.networks.iteritems():
            if not netdict['bridged']:
                yield network

    def getBridgelessNetworksForIface(self, iface):
        """ Return all bridgeless networks attached to nic/bond """
        for network, netdict in self.networks.iteritems():
            if not netdict['bridged'] and iface == netdict['iface']:
                yield network

    def getBridgedNetworksForIface(self, iface):
        """ Return all bridged networks attached to nic/bond """
        for bridge, netdict in self.networks.iteritems():
            if netdict['bridged'] and iface in netdict['ports']:
                yield bridge

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
                nic, vlan = port.split('.', 1)
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

    def nicOtherUsers(self, bridge, vlan, bonding, nic):
        """
        Returns a list of interfaces using a nic,
        other than the specified one.
        """
        if bonding:
            owner = bonding
        elif vlan:
            owner = nic + '.' + vlan
        else:
            owner = bridge
        users = self.ifaceUsers(nic)
        if bonding:
            users.update(self.bondingOtherUsers(bridge, vlan, bonding))
        users.discard(owner)
        return users

    def bondingOtherUsers(self, bridge, vlan, bonding):
        """
        Return a list of nics/interfaces using a bonding,
        other than the specified one.
        """
        if vlan:
            owner = bonding + '.' + vlan
        else:
            owner = bridge
        users = self.ifaceUsers(bonding)
        users.discard(owner)
        return users
