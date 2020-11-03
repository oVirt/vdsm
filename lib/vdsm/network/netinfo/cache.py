#
# Copyright 2015-2020 Red Hat, Inc.
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
from __future__ import division

import errno
import logging

import six

from vdsm.network import nmstate
from vdsm.network.ip.address import ipv6_supported
from vdsm.network.ipwrapper import getLinks
from vdsm.network.link import iface as link_iface
from vdsm.network.netconfpersistence import RunningConfig

from . import bonding
from . import bridges
from . import nics
from .addresses import getIpAddrs, getIpInfo, is_ipv6_local_auto
from .qos import report_network_qos
from .routes import get_routes, get_gateway, is_default_route


# By default all networks are 'legacy', it can be optionaly changed to 'ovs' in
# OVS capabilities handling.
# TODO: Get switch type from the system.
LEGACY_SWITCH = {'switch': 'legacy'}


class NetworkIsMissing(Exception):
    pass


def _get(vdsmnets=None):
    """
    Generate a networking report for all devices.
    In case vdsmnets is provided, it is used in the report instead of
    retrieving data from the running config.
    :return: Dict of networking devices with all their details.
    """
    ipaddrs = getIpAddrs()
    routes = get_routes()

    devices_info = _devices_report(ipaddrs, routes)
    nets_info = _networks_report(vdsmnets, routes, ipaddrs, devices_info)

    add_qos_info_to_devices(nets_info, devices_info)

    flat_devs_info = _get_flat_devs_info(devices_info)
    devices = _get_dev_names(nets_info, flat_devs_info)
    extra_info = _create_default_extra_info(devices)

    state = nmstate.state_show()
    extra_info.update(_get_devices_info_from_nmstate(state, devices))
    nameservers = nmstate.get_nameservers(state)

    _update_caps_info(nets_info, flat_devs_info, extra_info)

    networking_report = {'networks': nets_info}
    networking_report.update(devices_info)
    networking_report['nameservers'] = nameservers
    networking_report['supportsIPv6'] = ipv6_supported()

    return networking_report


def add_qos_info_to_devices(nets_info, devices_info):
    """ Update Qos data from networks on corresponding nic/bond """

    qos_list = _get_qos_info_from_net(nets_info)
    qos_list and _add_qos_info_to_southbound(qos_list, devices_info)


def _create_default_extra_info(devices):
    return {
        devname: {'dhcpv4': False, 'dhcpv6': False, 'ipv6autoconf': False}
        for devname in devices
    }


def _get_qos_info_from_net(nets_info):
    return [
        dict(
            host_qos=attrs['hostQos'],
            southbound=attrs['southbound'],
            net_name=net,
        )
        for net, attrs in six.viewitems(nets_info)
        if 'hostQos' in attrs
    ]


def _add_qos_info_to_southbound(qos_list, devices_info):
    for qos_dict in qos_list:
        southbound = qos_dict['southbound']
        host_qos = qos_dict['host_qos']
        net_name = qos_dict['net_name']
        vlan = '-1'

        if southbound in devices_info['vlans']:
            southbound, vlan = southbound.rsplit('.', 1)

        if southbound in devices_info['nics']:
            devices = devices_info['nics']
        elif southbound in devices_info['bondings']:
            devices = devices_info['bondings']
        else:
            logging.warning(
                'Qos exists on network {},'
                'but no corresponding nic/bond device ({})'
                'was found'.format(net_name, southbound)
            )
            continue

        devices_sb_info = devices[southbound]
        sb_qos_info = devices_sb_info.get('qos')

        if not sb_qos_info:
            sb_qos_info = devices_sb_info['qos'] = []

        qos_info = dict(hostQos=host_qos, vlan=int(vlan))

        sb_qos_info.append(qos_info)

    _sort_devices_qos_by_vlan(devices_info, 'nics')
    _sort_devices_qos_by_vlan(devices_info, 'bondings')


def _sort_devices_qos_by_vlan(devices_info, iface_type):
    for iface_attrs in six.viewvalues(devices_info[iface_type]):
        if 'qos' in iface_attrs:
            iface_attrs['qos'].sort(key=lambda k: (k['vlan']))


def _get_devices_info_from_nmstate(state, devices):
    return {
        ifname: {
            'dhcpv4': nmstate.is_dhcp_enabled(ifstate, nmstate.Interface.IPV4),
            'dhcpv6': nmstate.is_dhcp_enabled(ifstate, nmstate.Interface.IPV6),
            'ipv6autoconf': nmstate.is_autoconf_enabled(ifstate),
        }
        for ifname, ifstate in six.viewitems(
            nmstate.get_interfaces(state, filter=devices)
        )
    }


def _update_caps_info(nets_info, flat_devs_info, extra_info):
    for net_info in six.viewvalues(nets_info):
        net_info.update(extra_info[net_info['iface']])

    for devname, devinfo in six.viewitems(flat_devs_info):
        devinfo.update(extra_info[devname])


def _get_flat_devs_info(devices_info):
    return {
        devname: devinfo
        for sub_devs in six.viewvalues(devices_info)
        for devname, devinfo in six.viewitems(sub_devs)
    }


def _get_dev_names(nets_info, flat_devs_info):
    return {
        net_info['iface'] for net_info in six.viewvalues(nets_info)
    } | frozenset(flat_devs_info)


def _networks_report(vdsmnets, routes, ipaddrs, devices_info):
    if vdsmnets is None:
        running_nets = RunningConfig().networks
        nets_info = networks_base_info(running_nets, routes, ipaddrs)
    else:
        nets_info = vdsmnets

    for network_info in six.itervalues(nets_info):
        network_info.update(LEGACY_SWITCH)
        _update_net_southbound_info(network_info, devices_info)
        _update_net_vlanid_info(network_info, devices_info['vlans'])

    report_network_qos(nets_info, devices_info)

    return nets_info


def _update_net_southbound_info(network_info, devices_info):
    if network_info['bridged']:
        ports = set(network_info['ports'])
        for dev_type in ('bondings', 'nics', 'vlans'):
            sb_set = ports & set(devices_info[dev_type])
            if len(sb_set) == 1:
                network_info['southbound'], = sb_set
                return
        network_info['southbound'] = None
    else:
        network_info['southbound'] = network_info['iface']


def _update_net_vlanid_info(network_info, vlans_info):
    sb = network_info['southbound']
    if sb in vlans_info:
        network_info['vlanid'] = vlans_info[sb]['vlanid']


def _devices_report(ipaddrs, routes):
    devs_report = {'bondings': {}, 'bridges': {}, 'nics': {}, 'vlans': {}}

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
            devinfo = devs_report['vlans'][dev.name] = {
                'iface': dev.device,
                'vlanid': dev.vlanid,
            }
        else:
            continue
        devinfo.update(_devinfo(dev, routes, ipaddrs))

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


def networks_base_info(running_nets, routes=None, ipaddrs=None):
    if routes is None:
        routes = get_routes()
    if ipaddrs is None:
        ipaddrs = getIpAddrs()

    info = {}
    for net, attrs in six.viewitems(running_nets):
        if attrs.get('switch') != 'legacy':
            continue
        iface = get_net_iface_from_config(net, attrs)
        try:
            if not link_iface.iface(iface).exists():
                raise NetworkIsMissing('Iface %s was not found' % iface)
            info[net] = _getNetInfo(iface, attrs['bridged'], routes, ipaddrs)
        except NetworkIsMissing:
            # Missing networks are ignored, reporting only what exists.
            logging.warning('Missing network detected [%s]: %s', net, attrs)

    return info


def get_net_iface_from_config(net, netattrs):
    if netattrs['bridged']:
        return net
    iface = netattrs.get('bonding') or netattrs.get('nic')
    if 'vlan' in netattrs:
        iface = '{}.{}'.format(iface, netattrs['vlan'])

    return iface


def _devinfo(link, routes, ipaddrs):
    gateway = get_gateway(routes, link.name)
    ipv4addr, ipv4netmask, ipv4addrs, ipv6addrs = getIpInfo(
        link.name, ipaddrs, gateway
    )

    return {
        'addr': ipv4addr,
        'ipv4addrs': ipv4addrs,
        'ipv6addrs': ipv6addrs,
        'ipv6autoconf': is_ipv6_local_auto(link.name),
        'gateway': gateway,
        'ipv6gateway': get_gateway(routes, link.name, family=6),
        'mtu': link.mtu,
        'netmask': ipv4netmask,
        'ipv4defaultroute': is_default_route(gateway, routes),
    }


def _getNetInfo(iface, bridged, routes, ipaddrs):
    """Returns a dictionary of properties about the network's interface status.
    Raises a NetworkIsMissing if the iface does not exist."""
    data = {}
    try:
        if bridged:
            data.update(
                {
                    'ports': bridges.ports(iface),
                    'stp': bridges.stp_state(iface),
                }
            )
        else:
            # ovirt-engine-3.1 expects to see the "interface" attribute iff the
            # network is bridgeless. Please remove the attribute and this
            # comment when the version is no longer supported.
            data['interface'] = iface

        gateway = get_gateway(routes, iface)
        ipv4addr, ipv4netmask, ipv4addrs, ipv6addrs = getIpInfo(
            iface, ipaddrs, gateway
        )

        data.update(
            {
                'iface': iface,
                'bridged': bridged,
                'addr': ipv4addr,
                'netmask': ipv4netmask,
                'ipv4addrs': ipv4addrs,
                'ipv6addrs': ipv6addrs,
                'ipv6autoconf': is_ipv6_local_auto(iface),
                'gateway': gateway,
                'ipv6gateway': get_gateway(routes, iface, family=6),
                'ipv4defaultroute': is_default_route(gateway, routes),
                'mtu': link_iface.iface(iface).mtu(),
            }
        )
    except (IOError, OSError) as e:
        if e.errno == errno.ENOENT or e.errno == errno.ENODEV:
            logging.info('Obtaining info for net %s.', iface, exc_info=True)
            raise NetworkIsMissing('Network %s was not found' % iface)
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

    def getNicsVlanAndBondingForNetwork(self, network):
        vlan = None
        bonding = None
        lnics = []

        net_sb = self.networks[network]['southbound']
        vlanid = self.networks[network].get('vlanid')
        if vlanid is not None:
            vlan = net_sb
            sb = self.vlans[net_sb]['iface']
        else:
            sb = net_sb
        if sb in self.bondings:
            bonding = sb
            lnics = self.bondings[bonding]['slaves']
        elif sb in self.nics:
            lnics = [sb]

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
