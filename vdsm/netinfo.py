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

import os, glob, subprocess
import shlex
import logging
from fnmatch import fnmatch

import constants
from config import config

NET_CONF_DIR = '/etc/sysconfig/network-scripts/'
NET_CONF_BACK_DIR = constants.P_VDSM_LIB + 'netconfback/'

NET_CONF_PREF = NET_CONF_DIR + 'ifcfg-'
PROC_NET_VLAN = '/proc/net/vlan/'

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

def isbonding(dev):
    return os.path.exists('/sys/class/net/%s/bonding' % dev)

def operstate(dev):
    return file('/sys/class/net/%s/operstate' % dev).read().strip()

def speed(dev):
    try:
        try:
            if not os.path.exists('/sys/class/net/%s/bonding' % dev):
                s = int(file('/sys/class/net/%s/speed' % dev).read())
                if s in (2**16 - 1, 2**32 - 1) or s < 0:
                    return 0
                else:
                    return s
        except IOError, e:
            if e.errno == os.errno.EINVAL and (isbonding(dev) or
                                               operstate(dev) != 'up'):
                # this error is expected for bonding and devices that are down
                pass
            else:
                raise
    except:
        logging.error('cannot read %s speed', dev, exc_info=True)
    return 0

def ifconfig():
    """ Partial parser to ifconfig output """

    p = subprocess.Popen([constants.EXT_IFCONFIG, '-a'],
            close_fds=True, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE)
    out, err = p.communicate()
    ifaces = {}
    for ifaceblock in out.split('\n\n'):
        if not ifaceblock: continue
        addr = netmask = hwaddr = ''
        for line in ifaceblock.splitlines():
            if line[0] != ' ':
                ls = line.split()
                name = ls[0]
                if ls[2] == 'encap:Ethernet' and ls[3] == 'HWaddr':
                    hwaddr = ls[4]
            if line.startswith('          inet addr:'):
                sp = line.split()
                for col in sp:
                    if ':' not in col: continue
                    k, v = col.split(':')
                    if k == 'addr':
                        addr = v
                    if k == 'Mask':
                        netmask = v
        ifaces[name] = {'addr': addr, 'netmask': netmask, 'hwaddr': hwaddr}
    return ifaces

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
    """Return the (vlan, bonding, nics) tupple that belogs to bridge."""

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
    ifaces = ifconfig()
    routes = getRoutes()
    # FIXME handle bridge/nic missing from ifconfig
    d['networks'] = dict([ (bridge, {'ports': ports(bridge),
                                     'stp': bridge_stp_state(bridge),
                                     'addr': ifaces[bridge]['addr'],
                                     'netmask': ifaces[bridge]['netmask'],
                                     'gateway': routes.get(bridge, '0.0.0.0'),
                                     'cfg': getIfaceCfg(bridge),
                                     'mtu': getMtu(bridge)})
                           for bridge in bridges() ])
    d['nics'] = dict([ (nic, {'speed': speed(nic),
                              'addr': ifaces[nic]['addr'],
                              'netmask': ifaces[nic]['netmask'],
                              'hwaddr': ifaces[nic]['hwaddr'],
                              'mtu': getMtu(nic)})
                        for nic in nics() ])
    paddr = permAddr()
    for nic, nd in d['nics'].iteritems():
        if paddr.get(nic):
            nd['permhwaddr'] = paddr[nic]
    d['bondings'] = dict([ (bond, {'slaves': slaves(bond),
                              'addr': ifaces[bond]['addr'],
                              'netmask': ifaces[bond]['netmask'],
                              'hwaddr': ifaces[bond]['hwaddr'],
                              'cfg': getIfaceCfg(bond),
                              'mtu': getMtu(bond)})
                        for bond in bondings() ])
    d['vlans'] = dict([ (vlan, {'iface': vlan.split('.')[0],
                                'addr': ifaces[vlan]['addr'],
                                'netmask': ifaces[vlan]['netmask'],
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
    return filter(None, [i['addr'] for i in ifconfig().itervalues()])

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
            if nic in netdict['ports']:
                yield bridge

    def getBondingsForNic(self, nic):
        for b, bdict in self.bondings.iteritems():
            if nic in bdict['slaves']:
                yield b

    def getBondingForNic(self, nic):
        bondings = list(self.getBondingsForNic(nic))
        if bondings:
            assert len(bondings) == 1, "Unexpected configuration: More than one bonding per nic"
            return bondings[0]
        return None

    def getNicsVlanAndBondingForNetwork(self, network):
        vlan = None
        bonding = None
        nics = []
        for port in self.networks[network]['ports']:
            if port in self.vlans:
                assert vlan is None
                nic, vlan = port.split('.',1)
                assert self.vlans[port]['iface'] == nic
                port = nic
            if port in self.bondings:
                assert bonding is None
                bonding = port
                nics += self.bondings[bonding]['slaves']
            else:
                nics.append(port)
        return nics, vlan, bonding

