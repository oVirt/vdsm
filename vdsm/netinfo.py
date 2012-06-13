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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301  USA
#
# Refer to the README and COPYING files for full details of the license
#

import os
import glob
import ethtool
import shlex
import logging
from fnmatch import fnmatch
from xml.dom import minidom

import libvirtconnection

import constants
from config import config

NET_CONF_DIR = '/etc/sysconfig/network-scripts/'
NET_CONF_BACK_DIR = constants.P_VDSM_LIB + 'netconfback/'

NET_CONF_PREF = NET_CONF_DIR + 'ifcfg-'
PROC_NET_VLAN = '/proc/net/vlan/'

LIBVIRT_NET_PREFIX = 'vdsm-'

def nics():
    res = []
    for b in glob.glob('/sys/class/net/*/device'):
        nic = b.split('/')[-2]
        if not any(map(lambda p: fnmatch(nic, p),
                       config.get('vars', 'hidden_nics').split(',')) ):
            res.append(nic)
    return res

def bondings():
    return [ b.split('/')[-2] for b in glob.glob('/sys/class/net/*/bonding')]

def vlans():
    return [ b.split('/')[-1] for b in glob.glob('/sys/class/net/*.*')]

def bridges():
    return [ b.split('/')[-2] for b in glob.glob('/sys/class/net/*/bridge')]

def networks():
    """
    Get dict of networks from libvirt

    :returns: dict of networkname={properties}
    :rtype: dict of dict
            { 'ovirtmgmt': { 'bridge': 'ovirtmgmt', 'bridged': True },
              'red': { 'interface': 'red', 'bridged': False } }
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
                nets[netname]['interface'] = interfaces[0].getAttribute('dev')
                nets[netname]['bridged'] = False
            else:
                nets[netname]['bridge'] = xml.getElementsByTagName('bridge')[0].getAttribute('name')
                nets[netname]['bridged'] = True
    return nets

def slaves(bonding):
    return [ b.split('/')[-1].split('_', 1)[-1] for b in
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
    dev_info_list = ethtool.get_interfaces_info(dev)
    addr = dev_info_list[0].ipv4_address
    if addr is None:
        addr = ''
    return addr

def bitmask_to_address(bitmask):
    binary = ~((1L << (32-bitmask)) - 1)
    return ".".join(map(lambda x: str(binary>>(x<<3) & 0xff), [3, 2, 1, 0]))

def getnetmask(dev):
    dev_info_list = ethtool.get_interfaces_info(dev)
    netmask = dev_info_list[0].ipv4_netmask
    if netmask == 0:
        return ''
    return bitmask_to_address(netmask)

def gethwaddr(dev):
    return file('/sys/class/net/%s/address' % dev).read().strip()

def graph():
    for bridge in bridges():
        print bridge
        for iface in os.listdir('/sys/class/net/' + bridge + '/brif'):
            print '\t' + iface
            if iface in vlans():
                iface = iface.split('.')[0]
            if iface in bondings():
                for slave in slaves(iface):
                    print '\t\t' + slave

def getVlanBondingNic(bridge):
    """Return the (vlan, bonding, nics) tupple that belongs to bridge."""

    if bridge not in bridges():
        raise ValueError, 'unknown bridge %s' % (bridge,)
    vlan = bonding = ''
    nics = []
    for iface in os.listdir('/sys/class/net/' + bridge + '/brif'):
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
        route_file.readline() # skip header line

        for route_line in route_file.xreadlines():
            route_parm = route_line.rstrip().split('\t')

            if route_parm[1] == '00000000' and route_parm[2] != '00000000':
                ip_num = int(route_parm[2], 16)
                gateways[route_parm[0]] = intToAddress(ip_num)

    return gateways

def getIfaceCfg(iface):
    d = {}
    try:
        for line in open(NET_CONF_PREF + iface).readlines():
            line = line.strip()
            if line.startswith('#'): continue
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

def get():
    d = {}
    routes = getRoutes()
    # FIXME handle bridge/nic missing from ifconfig
    d['networks'] = {}
    nets = networks()
    for netname in nets.iterkeys():
        d['networks'][netname] = {}
        if nets[netname]['bridged']:
            d['networks'][netname] = { 'ports': ports(netname),
                    'stp': bridge_stp_state(netname),
                    'addr': getaddr(netname),
                    'netmask': getnetmask(netname),
                    'gateway': routes.get(netname, '0.0.0.0'),
                    'mtu': getMtu(netname), 'cfg': getIfaceCfg(netname) }
        else:
            d['networks'][netname] = { 'interface': nets[netname]['interface'] }
        d['networks'][netname]['bridged'] = nets[netname]['bridged']
    d['nics'] = dict([ (nic, {'speed': speed(nic),
                              'addr': getaddr(nic),
                              'netmask': getnetmask(nic),
                              'hwaddr': gethwaddr(nic),
                              'mtu': getMtu(nic)})
                        for nic in nics() ])
    paddr = permAddr()
    for nic, nd in d['nics'].iteritems():
        if paddr.get(nic):
            nd['permhwaddr'] = paddr[nic]
    d['bondings'] = dict([ (bond, {'slaves': slaves(bond),
                              'addr': getaddr(bond),
                              'netmask': getnetmask(bond),
                              'hwaddr': gethwaddr(bond),
                              'cfg': getIfaceCfg(bond),
                              'mtu': getMtu(bond)})
                        for bond in bondings() ])
    d['vlans'] = dict([ (vlan, {'iface': vlan.split('.')[0],
                                'addr': getaddr(vlan),
                                'netmask': getnetmask(vlan),
                                'mtu': getMtu(vlan)})
                        for vlan in vlans() ])
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
    return filter(None, [ getaddr(i) for i in ethtool.get_active_devices() ])

class NetInfo(object):
    def __init__(self, _netinfo=None):
        if _netinfo is None:
            _netinfo = get()

        self.networks = _netinfo['networks']
        self.vlans = _netinfo['vlans']
        self.nics = _netinfo['nics']
        self.bondings = _netinfo['bondings']

    def getNetworksAndVlansForBonding(self, bonding):
        """Returns tuples of (bridge, vlan) connected to bonding

        Note that only one bridge (or multiple vlans) should be connected to the same bonding.
        """
        for bridge, netdict in self.networks.iteritems():
            if netdict['bridged']:
                for iface in netdict['ports']:
                    if iface == bonding:
                        yield (bridge, None)
                    elif iface.startswith(bonding + '.'):
                        yield (bridge, iface.split('.',1)[1])

    def getVlansForNic(self, nic):
        for v, vdict in self.vlans.iteritems():
            if nic == vdict['iface']:
                yield v

    def getNetworksForNic(self, nic):
        for bridge, netdict in self.networks.iteritems():
            if netdict['bridged'] and nic in netdict['ports']:
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
            assert len(bondings) == 1, "Unexpected configuration: More than one bonding per nic"
            return bondings[0]
        return None

    def getNicsVlanAndBondingForNetwork(self, network):
        vlan = None
        bonding = None
        lnics = []

        if self.networks[network]['bridged']:
            ports =  self.networks[network]['ports']
        else:
            ports = []
            interface = self.networks[network]['interface']
            ports.append(interface)

        for port in ports:
            if port in self.vlans:
                assert vlan is None
                nic, vlan = port.split('.',1)
                assert self.vlans[port]['iface'] == nic
                port = nic
            if port in self.bondings:
                assert bonding is None
                bonding = port
                lnics += self.bondings[bonding]['slaves']
            else:
                lnics.append(port)

        return lnics, vlan, bonding

    def getBridgelessNetworks(self):
        """
        Get list of birdgeless networks

        :returns: list of networks name
        :rtype: List
        """
        return [ netname for (netname, net) in networks().iteritems() if not 'bridge' in net ]
