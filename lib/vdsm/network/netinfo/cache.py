#
# Copyright 2015-2016 Red Hat, Inc.
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
import logging
import os
import errno
import six

from vdsm.network import libvirt
from vdsm.network import netinfo
from vdsm.network.ip.address import ipv6_supported
from vdsm.network.ip import dhclient
from vdsm.network.ipwrapper import getLinks
from vdsm.network.netlink import link as nl_link

from .addresses import getIpAddrs, getIpInfo, is_ipv6_local_auto
from . import bonding
from . import bridges
from .dns import get_host_nameservers
from .mtus import getMtu
from . import nics
from . import vlans
from .routes import get_routes, get_gateway, is_default_route
from .qos import report_network_qos


# By default all networks are 'legacy', it can be optionaly changed to 'ovs' in
# OVS capabilities handling.
# TODO: Get switch type from the system.
LEGACY_SWITCH = {'switch': 'legacy'}


def _get(vdsmnets=None):
    """
    Generate a networking report for all devices, including data managed by
    libvirt.
    In case vdsmnets is provided, it is used in the report instead of
    retrieving data from libvirt.
    :return: Dict of networking devices with all their details.
    """
    ipaddrs = getIpAddrs()
    routes = get_routes()

    devices_info = _devices_report(ipaddrs, routes)
    nets_info = _networks_report(vdsmnets, routes, ipaddrs, devices_info)

    networking_report = {'networks': nets_info}
    networking_report.update(devices_info)

    networking_report['nameservers'] = get_host_nameservers()
    networking_report['supportsIPv6'] = ipv6_supported()

    return networking_report


def _networks_report(vdsmnets, routes, ipaddrs, devices_info):
    if vdsmnets is None:
        nets_info = libvirtNets2vdsm(libvirt.networks(), routes, ipaddrs)
    else:
        nets_info = vdsmnets

    ifaces = {net_info['iface'] for net_info in six.itervalues(nets_info)}
    dhcp_info = dhclient.dhcp_info(ifaces)

    for network_info in six.itervalues(nets_info):
        network_info.update(dhcp_info[network_info['iface']])
        network_info.update(LEGACY_SWITCH)

    report_network_qos(nets_info, devices_info)

    return nets_info


def _devices_report(ipaddrs, routes):
    devs_report = {'bondings': {}, 'bridges': {}, 'nics': {}, 'vlans': {}}

    devinfo_by_devname = {}
    for dev in (link for link in getLinks() if not link.isHidden()):
        if dev.isBRIDGE():
            devinfo = devs_report['bridges'][dev.name] = bridges.info(dev)
        elif dev.isNICLike():
            devinfo = devs_report['nics'][dev.name] = nics.info(dev)
            devinfo.update(bonding.get_bond_slave_agg_info(dev.name))
        elif dev.isBOND():
            devinfo = devs_report['bondings'][dev.name] = bonding.info(dev)
            devinfo.update(bonding.get_bond_agg_info(dev.name))
            devinfo.update(LEGACY_SWITCH)
        elif dev.isVLAN():
            devinfo = devs_report['vlans'][dev.name] = vlans.info(dev)
        else:
            continue
        devinfo.update(_devinfo(dev, routes, ipaddrs))
        devinfo_by_devname[dev.name] = devinfo

    dhcp_info = dhclient.dhcp_info(frozenset(devinfo_by_devname))
    for devname, devinfo in devinfo_by_devname.items():
        devinfo.update(dhcp_info[devname])

    _permanent_hwaddr_info(devs_report)

    return devs_report


def _permanent_hwaddr_info(devs_report):
    paddr = bonding.permanent_address()
    nics_info = devs_report.get('nics', {})
    for nic, nicinfo in six.viewitems(nics_info):
        if nic in paddr:
            nicinfo['permhwaddr'] = paddr[nic]


def get(vdsmnets=None, compatibility=None):
    if compatibility is None:
        return _get(vdsmnets)
    elif compatibility < 30700:
        # REQUIRED_FOR engine < 3.7
        return _stringify_mtus(_get(vdsmnets))

    return _get(vdsmnets)


def _stringify_mtus(netinfo_data):
    for devtype in ('bondings', 'bridges', 'networks', 'nics', 'vlans'):
        for dev in six.itervalues(netinfo_data[devtype]):
            dev['mtu'] = str(dev['mtu'])
    return netinfo_data


def libvirtNets2vdsm(nets, routes=None, ipAddrs=None):
    if routes is None:
        routes = get_routes()
    if ipAddrs is None:
        ipAddrs = getIpAddrs()

    d = {}
    for net, netAttr in six.iteritems(nets):
        try:
            # Pass the iface if the net is _not_ bridged, the bridge otherwise
            devname = netAttr.get('iface', net)
            d[net] = _getNetInfo(devname, netAttr['bridged'], routes, ipAddrs)
        except KeyError:
            continue  # Do not report missing libvirt networks.
    return d


def _devinfo(link, routes, ipaddrs):
    gateway = get_gateway(routes, link.name)
    ipv4addr, ipv4netmask, ipv4addrs, ipv6addrs = getIpInfo(
        link.name, ipaddrs, gateway)

    return {'addr': ipv4addr,
            'ipv4addrs': ipv4addrs,
            'ipv6addrs': ipv6addrs,
            'ipv6autoconf': is_ipv6_local_auto(link.name),
            'gateway': gateway,
            'ipv6gateway': get_gateway(routes, link.name, family=6),
            'mtu': link.mtu,
            'netmask': ipv4netmask,
            'ipv4defaultroute': is_default_route(gateway)}


def ifaceUsed(iface):
    """Lightweight implementation of bool(Netinfo.ifaceUsers()) that does not
    require a NetInfo object."""
    if os.path.exists(os.path.join(netinfo.NET_PATH, iface, 'brport')):
        return True  # Is it a port
    for linkDict in nl_link.iter_links():
        if linkDict['name'] == iface and 'master' in linkDict:  # Is it a slave
            return True
        if linkDict.get('device') == iface and linkDict.get('type') == 'vlan':
            return True  # it backs a VLAN
    for net_attr in six.itervalues(libvirt.networks()):
        if net_attr.get('iface') == iface:
            return True
    return False


def _getNetInfo(iface, bridged, routes, ipaddrs):
    """Returns a dictionary of properties about the network's interface status.
    Raises a KeyError if the iface does not exist."""
    data = {}
    try:
        if bridged:
            data.update({'ports': bridges.ports(iface),
                         'stp': bridges.stp_state(iface)})
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
                     'ipv4addrs': ipv4addrs,
                     'ipv6addrs': ipv6addrs,
                     'ipv6autoconf': is_ipv6_local_auto(iface),
                     'gateway': gateway,
                     'ipv6gateway': get_gateway(routes, iface, family=6),
                     'ipv4defaultroute': is_default_route(gateway),
                     'mtu': getMtu(iface)})
    except (IOError, OSError) as e:
        if e.errno == errno.ENOENT:
            logging.info('Obtaining info for net %s.', iface, exc_info=True)
            raise KeyError('Network %s was not found' % iface)
        else:
            raise
    return data


class NetInfo(object):
    def __init__(self, _netinfo):
        self.networks = _netinfo['networks']
        self.vlans = _netinfo['vlans']
        self.nics = _netinfo['nics']
        self.bondings = _netinfo['bondings']
        self.bridges = _netinfo['bridges']
        self.nameservers = _netinfo['nameservers']

    def del_network(self, network):
        del self.networks[network]

    def del_bonding(self, bonding):
        del self.bondings[bonding]

    def del_vlan(self, vlan):
        del self.vlans[vlan]

    def del_bridge(self, bridge):
        self.bridges.pop(bridge, None)

    def getVlansForIface(self, iface):
        for vlandict in six.itervalues(self.vlans):
            if iface == vlandict['iface']:
                yield vlandict['vlanid']

    def getNetworkForIface(self, iface):
        """ Return the network attached to nic/bond """
        for network, netdict in six.iteritems(self.networks):
            if ('ports' in netdict and iface in netdict['ports'] or
                    'iface' in netdict and iface == netdict['iface']):
                return network

    def getBridgedNetworkForIface(self, iface):
        """ Return all bridged networks attached to nic/bond """
        for bridge, netdict in six.iteritems(self.networks):
            if netdict['bridged'] and iface in netdict['ports']:
                return bridge

    def getNicsForBonding(self, bond):
        bondAttrs = self.bondings[bond]
        return bondAttrs['slaves']

    def getBondingForNic(self, nic):
        bondings = [b for (b, attrs) in six.iteritems(self.bondings) if
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

        if self.networks[network]['switch'] == 'legacy':
            # TODO: CachingNetInfo should not use external resources in its
            # methods. Drop this branch when legacy netinfo report 'bond',
            # 'nics' and 'vlanid' as a part of network entries.
            if self.networks[network]['bridged']:
                ports = self.networks[network]['ports']
            else:
                ports = []
                interface = self.networks[network]['iface']
                ports.append(interface)

            for port in ports:
                if port in self.vlans:
                    nic = self.vlans[port]['iface']
                    vlanid = self.vlans[port]['vlanid']
                    vlan = port  # vlan devices can have an arbitrary name
                    port = nic
                if port in self.bondings:
                    bonding = port
                    lnics += self.bondings[bonding]['slaves']
                elif port in self.nics:
                    lnics.append(port)
        else:
            bonding = self.networks[network]['bond']
            lnics = self.networks[network]['nics']
            vlanid = self.networks[network].get('vlanid')
            vlan = ('%s.%s' % (bonding or lnics[0], vlanid)
                    if vlanid is not None else None)

        return lnics, vlan, vlanid, bonding

    def ifaceUsers(self, iface):
        "Returns a list of entities using the interface"
        users = set()
        for n, ndict in six.iteritems(self.networks):
            if ndict['bridged'] and iface in ndict['ports']:
                users.add(n)
            elif not ndict['bridged'] and iface == ndict['iface']:
                users.add(n)
        for b, bdict in six.iteritems(self.bondings):
            if iface in bdict['slaves']:
                users.add(b)
        for v, vdict in six.iteritems(self.vlans):
            if iface == vdict['iface']:
                users.add(v)
        return users


class CachingNetInfo(NetInfo):
    def __init__(self, _netinfo=None):
        if _netinfo is None:
            _netinfo = get()
        super(CachingNetInfo, self).__init__(_netinfo)

    def updateDevices(self):
        """
        Updates the object device information while keeping the cached network
        information.
        """
        _netinfo = get(vdsmnets=self.networks)
        self.networks = _netinfo['networks']
        self.vlans = _netinfo['vlans']
        self.nics = _netinfo['nics']
        self.bondings = _netinfo['bondings']
        self.bridges = _netinfo['bridges']
        self.nameservers = _netinfo['nameservers']
