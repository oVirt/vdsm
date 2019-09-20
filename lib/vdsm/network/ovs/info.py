# Copyright 2016-2017 Red Hat, Inc.
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

import six

from vdsm.network import cmd
from vdsm.network.ip import dhclient
from vdsm.network.netinfo.addresses import (
    getIpAddrs,
    getIpInfo,
    is_ipv6_local_auto,
)
from vdsm.network.netinfo.routes import (
    get_routes,
    get_gateway,
    is_default_route,
)
from vdsm.network.link.iface import iface as iflink
from . import driver


OVS_CTL = '/usr/share/openvswitch/scripts/ovs-ctl'


NORTHBOUND = 'northbound'
SOUTHBOUND = 'southbound'

EMPTY_PORT_INFO = {
    'addr': '',
    'ipv4addrs': [],
    'gateway': '',
    'ipv4defaultroute': False,
    'netmask': '',
    'dhcpv4': False,
    'ipv6addrs': [],
    'ipv6autoconf': False,
    'ipv6gateway': '',
    'dhcpv6': False,
}

SHARED_NETWORK_ATTRIBUTES = [
    'mtu',
    'addr',
    'ipv4addrs',
    'gateway',
    'ipv4defaultroute',
    'netmask',
    'dhcpv4',
    'ipv6addrs',
    'ipv6autoconf',
    'ipv6gateway',
    'dhcpv6',
]


def is_ovs_service_running():
    try:
        rc, _, _ = cmd.exec_sync([OVS_CTL, 'status'])
    except OSError as err:
        # Silently ignore the missing file and consider the service as down.
        if err.errno == errno.ENOENT:
            rc = errno.ENOENT
        else:
            raise
    return rc == 0


class OvsDB(object):
    def __init__(self, ovsdb):
        bridges_command = ovsdb.list_bridge_info()
        ports_command = ovsdb.list_port_info()
        ifaces_command = ovsdb.list_interface_info()

        with ovsdb.transaction() as transaction:
            transaction.add(bridges_command)
            transaction.add(ports_command)
            transaction.add(ifaces_command)

        self.bridges = bridges_command.result
        self.ports = ports_command.result
        self.ifaces = ifaces_command.result


class OvsInfo(object):
    def __init__(self):
        ovs_db = OvsDB(driver.create())
        self._ports_uuids = {port['_uuid']: port for port in ovs_db.ports}
        self._ifaces_uuids = {iface['_uuid']: iface for iface in ovs_db.ifaces}
        self._ifaces_macs = {
            iface['mac_in_use']: iface
            for iface in ovs_db.ifaces
            if iface['mac_in_use']
        }

        self._bridges = {
            bridge['name']: self._bridge_attr(bridge)
            for bridge in ovs_db.bridges
        }
        self._bridges_by_sb = self._get_bridges_by_sb()
        self._northbounds_by_sb = self._get_northbounds_by_sb()
        self._northbounds_by_bridges = self._get_northbounds_by_bridges()

    @property
    def bridges(self):
        return self._bridges

    @property
    def bridges_by_sb(self):
        return self._bridges_by_sb

    def _get_bridges_by_sb(self):
        bridges_by_sb = {}

        for bridge, attrs in six.iteritems(self.bridges):
            bridge_sb = self.southbound_port(attrs['ports'])
            if bridge_sb:
                bridges_by_sb[bridge_sb] = bridge

        return bridges_by_sb

    @property
    def northbounds_by_sb(self):
        return self._northbounds_by_sb

    def _get_northbounds_by_sb(self):
        northbounds_by_sb = {}

        for sb, bridge in six.iteritems(self.bridges_by_sb):
            bridge_ports = self.bridges[bridge]['ports']
            northbounds = self.northbound_ports(bridge_ports)
            northbounds_by_sb[sb] = set(northbounds)

        return northbounds_by_sb

    def _bridge_attr(self, bridge_entry):
        stp = bridge_entry['stp_enable']
        ports = [self._ports_uuids[uuid] for uuid in bridge_entry['ports']]
        ports_info = {port['name']: self._port_attr(port) for port in ports}
        dpdk_enabled = bridge_entry['datapath_type'] == 'netdev'

        return {'ports': ports_info, 'stp': stp, 'dpdk_enabled': dpdk_enabled}

    def _port_attr(self, port_entry):
        tag = port_entry['tag']
        level = port_entry['other_config'].get('vdsm_level')

        return {'tag': tag, 'level': level}

    @property
    def northbounds_by_bridges(self):
        return self._northbounds_by_bridges

    def _get_northbounds_by_bridges(self):
        return {
            self.bridges_by_sb[sb]: nbs
            for sb, nbs in six.iteritems(self.northbounds_by_sb)
        }

    @staticmethod
    def southbound_port(ports):
        return next(
            (
                port
                for port, attrs in six.iteritems(ports)
                if attrs['level'] == SOUTHBOUND
            ),
            None,
        )

    @staticmethod
    def northbound_ports(ports):
        return (
            port
            for port, attrs in six.iteritems(ports)
            if attrs['level'] == NORTHBOUND
        )


def get_netinfo():
    netinfo = create_netinfo(OvsInfo())
    netinfo.update(_fake_devices(netinfo['networks']))
    return netinfo


def bridge_info(net_name):
    ovs_info = OvsInfo()
    bridge_name = _northbound2bridge(net_name, ovs_info)
    if not bridge_name:
        return None
    br_info = ovs_info.bridges[bridge_name]
    return {'name': bridge_name, 'dpdk_enabled': br_info['dpdk_enabled']}


def _northbound2bridge(northbound, ovs_info):
    """Return the bridge to which northbound is connected."""
    bridges = ovs_info.bridges

    if northbound in bridges:
        return northbound

    for bridge, bridge_attrs in six.iteritems(bridges):
        ports = bridge_attrs['ports']
        if northbound in ovs_info.northbound_ports(ports):
            return bridge

    return None


def _fake_devices(networks):
    fake_devices = {'bridges': {}, 'vlans': {}}

    for net, attrs in six.iteritems(networks):
        fake_devices['bridges'][net] = _fake_bridge(attrs)
        vlanid = attrs.get('vlanid')
        if vlanid is not None:
            fake_devices['vlans'].update(_fake_vlan(attrs, vlanid))

    return fake_devices


def _fake_bridge(net_attrs):
    bridge_info = {'ports': net_attrs['ports'], 'stp': net_attrs['stp']}
    bridge_info.update(_shared_net_attrs(net_attrs))
    return bridge_info


def _fake_vlan(net_attrs, vlanid):
    iface = net_attrs['southbound']
    vlan_info = {'vlanid': vlanid, 'iface': iface, 'mtu': net_attrs['mtu']}
    vlan_info.update(EMPTY_PORT_INFO)
    vlan_name = '%s.%s' % (iface, vlanid)
    return {vlan_name: vlan_info}


def create_netinfo(ovs_info):
    addresses = getIpAddrs()
    routes = get_routes()

    _netinfo = {'networks': {}}

    for bridge, bridge_attrs in six.iteritems(ovs_info.bridges):
        ports = bridge_attrs['ports']

        southbound = ovs_info.southbound_port(ports)

        # northbound ports represents networks
        stp = bridge_attrs['stp']
        for northbound_port in ovs_info.northbound_ports(ports):
            _netinfo['networks'][northbound_port] = _get_network_info(
                northbound_port,
                bridge,
                southbound,
                ports,
                stp,
                addresses,
                routes,
            )

    return _netinfo


def _get_network_info(
    northbound, bridge, southbound, ports, stp, addresses, routes
):
    tag = ports[northbound]['tag']
    network_info = {
        'iface': northbound,
        'bridged': True,
        'southbound': southbound,
        'ports': _get_net_ports(bridge, northbound, southbound, tag, ports),
        'stp': stp,
        'switch': 'ovs',
    }
    if tag is not None:
        # TODO: We should always report vlan, even if it is None. Netinfo
        # should be canonicalized before passed to caps, so None will not be
        # exposed in API call result.
        network_info['vlanid'] = tag
    network_info.update(_get_iface_info(northbound, addresses, routes))
    return network_info


def _get_net_ports(bridge, northbound, southbound, net_tag, ports):
    if net_tag:
        net_southbound_port = '{}.{}'.format(southbound, net_tag)
    else:
        net_southbound_port = southbound

    net_ports = [net_southbound_port]
    net_ports += [
        port
        for port, port_attrs in six.iteritems(ports)
        if (
            port_attrs['tag'] == net_tag
            and port != bridge
            and port_attrs['level'] not in (SOUTHBOUND, NORTHBOUND)
        )
    ]

    return net_ports


def _get_iface_info(iface, addresses, routes):
    ipv4gateway = get_gateway(routes, iface, family=4)
    ipv4addr, ipv4netmask, ipv4addrs, ipv6addrs = getIpInfo(
        iface, addresses, ipv4gateway
    )
    is_dhcpv4 = dhclient.is_active(iface, family=4)
    is_dhcpv6 = dhclient.is_active(iface, family=6)
    mtu = iflink(iface).mtu()
    return {
        'mtu': mtu,
        'addr': ipv4addr,
        'ipv4addrs': ipv4addrs,
        'gateway': ipv4gateway,
        'netmask': ipv4netmask,
        'ipv4defaultroute': is_default_route(ipv4gateway, routes),
        'dhcpv4': is_dhcpv4,
        'ipv6addrs': ipv6addrs,
        'ipv6gateway': get_gateway(routes, iface, family=6),
        'ipv6autoconf': is_ipv6_local_auto(iface),
        'dhcpv6': is_dhcpv6,
    }


def fake_bridgeless(ovs_netinfo, kernel_netinfo, running_bridgeless_networks):
    """
    An OVS setup does not support bridgeless networks. Requested bridgeless
    networks (as seen in running_config) are faked to appear as if they are
    bridgeless. Faking involves modifying the netinfo report, removing the
    faked bridge and creating the faked device that replaces it (vlan, bond
    or a nic).
    """
    nics_netinfo = kernel_netinfo['nics']
    bonds_netinfo = kernel_netinfo['bondings']

    for net in running_bridgeless_networks:
        net_attrs = ovs_netinfo['networks'][net]
        iface_type, iface_name = _bridgeless_fake_iface(
            net_attrs, bonds_netinfo
        )

        # NICs and BONDs are kernel devices, VLANs are OVS devices.
        if iface_type == 'nics':
            devtype_netinfo = nics_netinfo
        elif iface_type == 'bondings':
            devtype_netinfo = bonds_netinfo
        else:
            devtype_netinfo = ovs_netinfo[iface_type]

        devtype_netinfo[iface_name].update(_shared_net_attrs(net_attrs))

        ovs_netinfo['networks'][net]['iface'] = iface_name

        ovs_netinfo['bridges'].pop(net)
        ovs_netinfo['networks'][net]['bridged'] = False


def _bridgeless_fake_iface(net_attrs, bonds_info):
    vlanid = net_attrs.get('vlanid')
    sb = net_attrs['southbound']

    if vlanid is not None:
        iface_type = 'vlans'
        iface_name = '{}.{}'.format(sb, vlanid)
    elif sb in bonds_info:
        iface_type = 'bondings'
        iface_name = sb
    else:
        iface_type = 'nics'
        iface_name = sb

    return iface_type, iface_name


def _shared_net_attrs(attrs):
    return {key: attrs[key] for key in SHARED_NETWORK_ATTRIBUTES}
