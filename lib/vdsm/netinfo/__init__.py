#
# Copyright 2015 Red Hat, Inc.
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
from itertools import chain
import logging
import os
import errno
import six
import xml.etree.cElementTree as etree

from ..ipwrapper import getLinks, DUMMY_BRIDGE
from .. import libvirtconnection
from ..netconfpersistence import RunningConfig
from ..netlink import link as nl_link

from .addresses import getIpAddrs, getIpInfo
from .bonding import permanent_address, bondinfo, bondOptsCompat
from .bridges import bridgeinfo, ports, bridge_stp_state
from .dhcp import (get_dhclient_ifaces, propose_updates_to_reported_dhcp,
                   update_reported_dhcp, dhcp_used)
from .misc import getIfaceCfg
from .mtus import getMtu
from .nics import nicinfo
from .routes import get_routes, get_gateway
from .qos import report_network_qos
from .vlans import vlaninfo, vlan_id, vlan_device


NET_PATH = '/sys/class/net'

LIBVIRT_NET_PREFIX = 'vdsm-'


DUMMY_BRIDGE  # Appease flake8 since dummy bridge should be exported from here


def get(vdsmnets=None):
    networking = {'bondings': {}, 'bridges': {}, 'networks': {}, 'nics': {},
                  'vlans': {}}
    paddr = permanent_address()
    ipaddrs = getIpAddrs()
    dhcpv4_ifaces, dhcpv6_ifaces = get_dhclient_ifaces()
    routes = get_routes()
    running_config = RunningConfig()

    if vdsmnets is None:
        libvirt_nets = networks()
        networking['networks'] = libvirtNets2vdsm(libvirt_nets, running_config,
                                                  routes, ipaddrs,
                                                  dhcpv4_ifaces, dhcpv6_ifaces)
    else:
        networking['networks'] = vdsmnets

    for dev in (link for link in getLinks() if not link.isHidden()):
        if dev.isBRIDGE():
            devinfo = networking['bridges'][dev.name] = bridgeinfo(dev)
        elif dev.isNICLike():
            devinfo = networking['nics'][dev.name] = nicinfo(dev, paddr)
        elif dev.isBOND():
            devinfo = networking['bondings'][dev.name] = bondinfo(dev)
        elif dev.isVLAN():
            devinfo = networking['vlans'][dev.name] = vlaninfo(dev)
        else:
            continue
        devinfo.update(_devinfo(dev, routes, ipaddrs, dhcpv4_ifaces,
                                dhcpv6_ifaces))
        if dev.isBOND():
            bondOptsCompat(devinfo)

    for network_name, network_info in six.iteritems(networking['networks']):
        if network_info['bridged']:
            network_info['cfg'] = networking['bridges'][network_name]['cfg']
        updates = propose_updates_to_reported_dhcp(network_info, networking)
        update_reported_dhcp(updates, networking)

    report_network_qos(networking)

    return networking


def libvirtNets2vdsm(nets, running_config=None, routes=None, ipAddrs=None,
                     dhcpv4_ifaces=None, dhcpv6_ifaces=None):
    if running_config is None:
        running_config = RunningConfig()
    if routes is None:
        routes = get_routes()
    if ipAddrs is None:
        ipAddrs = getIpAddrs()
    if dhcpv4_ifaces is None or dhcpv6_ifaces is None:
        dhcpv4_ifaces, dhcpv6_ifaces = get_dhclient_ifaces()
    d = {}
    for net, netAttr in nets.iteritems():
        try:
            # Pass the iface if the net is _not_ bridged, the bridge otherwise
            d[net] = _getNetInfo(netAttr.get('iface', net), netAttr['bridged'],
                                 routes, ipAddrs, dhcpv4_ifaces, dhcpv6_ifaces,
                                 running_config.networks.get(net, None))
        except KeyError:
            continue  # Do not report missing libvirt networks.
    return d


def _devinfo(link, routes, ipaddrs, dhcpv4_ifaces, dhcpv6_ifaces):
    gateway = get_gateway(routes, link.name)
    ipv4addr, ipv4netmask, ipv4addrs, ipv6addrs = getIpInfo(
        link.name, ipaddrs, gateway)
    info = {'addr': ipv4addr,
            'cfg': getIfaceCfg(link.name),
            'ipv4addrs': ipv4addrs,
            'ipv6addrs': ipv6addrs,
            'gateway': gateway,
            'ipv6gateway': get_gateway(routes, link.name, family=6),
            'dhcpv4': link.name in dhcpv4_ifaces,  # to be refined if a network
            'dhcpv6': link.name in dhcpv6_ifaces,  # is not configured for DHCP
            'mtu': str(link.mtu),
            'netmask': ipv4netmask}
    if 'BOOTPROTO' not in info['cfg']:
        info['cfg']['BOOTPROTO'] = 'dhcp' if info['dhcpv4'] else 'none'
    return info


def ifaceUsed(iface):
    """Lightweight implementation of bool(Netinfo.ifaceUsers()) that does not
    require a NetInfo object."""
    if os.path.exists(os.path.join(NET_PATH, iface, 'brport')):  # Is it a port
        return True
    for linkDict in nl_link.iter_links():
        if linkDict['name'] == iface and 'master' in linkDict:  # Is it a slave
            return True
        if linkDict.get('device') == iface and linkDict.get('type') == 'vlan':
            return True  # it backs a VLAN
    for name, info in networks().iteritems():
        if info.get('iface') == iface:
            return True
    return False


def networks():
    """
    Get dict of networks from libvirt

    :returns: dict of networkname={properties}
    :rtype: dict of dict
            { 'ovirtmgmt': { 'bridge': 'ovirtmgmt', 'bridged': True}
              'red': { 'iface': 'red', 'bridged': False}}
    """
    nets = {}
    conn = libvirtconnection.get()
    allNets = ((net, net.name()) for net in conn.listAllNetworks(0))
    for net, netname in allNets:
        if netname.startswith(LIBVIRT_NET_PREFIX):
            netname = netname[len(LIBVIRT_NET_PREFIX):]
            nets[netname] = {}
            xml = etree.fromstring(net.XMLDesc(0))
            interface = xml.find('.//interface')
            if interface is not None:
                nets[netname]['iface'] = interface.get('dev')
                nets[netname]['bridged'] = False
            else:
                nets[netname]['bridge'] = xml.find('.//bridge').get('name')
                nets[netname]['bridged'] = True
    return nets


def _getNetInfo(iface, bridged, routes, ipaddrs, dhcpv4_ifaces, dhcpv6_ifaces,
                net_attrs):
    """Returns a dictionary of properties about the network's interface status.
    Raises a KeyError if the iface does not exist."""
    data = {}
    try:
        if bridged:
            data.update({'ports': ports(iface),
                         'stp': bridge_stp_state(iface)})
        else:
            # ovirt-engine-3.1 expects to see the "interface" attribute iff the
            # network is bridgeless. Please remove the attribute and this
            # comment when the version is no longer supported.
            data['interface'] = iface

        gateway = get_gateway(routes, iface)
        ipv4addr, ipv4netmask, ipv4addrs, ipv6addrs = getIpInfo(
            iface, ipaddrs, gateway)
        data.update({'iface': iface, 'bridged': bridged,
                     'addr': ipv4addr, 'netmask': ipv4netmask,
                     'dhcpv4': dhcp_used(iface, dhcpv4_ifaces, net_attrs),
                     'dhcpv6': dhcp_used(iface, dhcpv6_ifaces, net_attrs,
                                         family=6),
                     'ipv4addrs': ipv4addrs,
                     'ipv6addrs': ipv6addrs,
                     'gateway': gateway,
                     'ipv6gateway': get_gateway(routes, iface, family=6),
                     'mtu': str(getMtu(iface))})
    except (IOError, OSError) as e:
        if e.errno == errno.ENOENT:
            logging.info('Obtaining info for net %s.', iface, exc_info=True)
            raise KeyError('Network %s was not found' % iface)
        else:
            raise
    return data


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
        return chain(self._getBridgedNetworksAndVlansForIface(iface),
                     self._getBridgelessNetworksAndVlansForIface(iface))

    def _getBridgedNetworksAndVlansForIface(self, iface):
        """ Returns tuples of (bridge, vlan) connected to nic/bond """
        for network, netdict in self.networks.iteritems():
            if netdict['bridged']:
                for interface in netdict['ports']:
                    if iface == interface:
                        yield (network, None)
                    elif interface.startswith(iface + '.'):
                        yield (network, vlan_id(interface))

    def _getBridgelessNetworksAndVlansForIface(self, iface):
        """ Returns tuples of (network, vlan) connected to nic/bond """
        for network, netdict in self.networks.iteritems():
            if not netdict['bridged']:
                if iface == netdict['iface']:
                    yield (network, None)
                elif netdict['iface'].startswith(iface + '.'):
                    yield (network, vlan_id(netdict['iface']))

    def getVlansForIface(self, iface):
        for vlandict in six.itervalues(self.vlans):
            if iface == vlandict['iface']:
                yield vlandict['vlanid']

    def getNetworkForIface(self, iface):
        """ Return the network attached to nic/bond """
        for network, netdict in self.networks.iteritems():
            if ('ports' in netdict and iface in netdict['ports'] or
                    iface == netdict['iface']):
                return network

    def getBridgedNetworkForIface(self, iface):
        """ Return all bridged networks attached to nic/bond """
        for bridge, netdict in self.networks.iteritems():
            if netdict['bridged'] and iface in netdict['ports']:
                return bridge

    def getNicsForBonding(self, bond):
        bondAttrs = self.bondings[bond]
        return bondAttrs['slaves']

    def getBondingForNic(self, nic):
        bondings = [b for (b, attrs) in self.bondings.iteritems() if
                    nic in attrs['slaves']]
        if bondings:
            assert len(bondings) == 1, \
                "Unexpected configuration: More than one bonding per nic"
            return bondings[0]
        return None

    def getNicsVlanAndBondingForNetwork(self, network):
        vlan = None
        vlanid = None
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
                nic = vlan_device(port)
                vlanid = vlan_id(port)
                vlan = port  # vlan devices can have an arbitrary name
                assert self.vlans[port]['iface'] == nic
                port = nic
            if port in self.bondings:
                assert bonding is None
                bonding = port
                lnics += self.bondings[bonding]['slaves']
            elif port in self.nics:
                lnics.append(port)

        return lnics, vlan, vlanid, bonding

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
