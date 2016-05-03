# Copyright 2016 Red Hat, Inc.
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

import six

from vdsm.netinfo.addresses import getIpAddrs, getIpInfo, is_ipv6_local_auto
from vdsm.netinfo.dhcp import dhcp_status
from vdsm.netinfo.mtus import getMtu
from vdsm.netinfo.routes import get_routes, get_gateway

from . import driver


NORTHBOUND = 'northbound'
SOUTHBOUND = 'southbound'

EMPTY_PORT_INFO = {
    'mtu': 1500,
    'addr': '',
    'ipv4addrs': [],
    'gateway': '',
    'netmask': '',
    'dhcpv4': False,
    'ipv6addrs': [],
    'ipv6autoconf': False,
    'ipv6gateway': '',
    'dhcpv6': False
}


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
        self._ifaces_macs = {iface['mac_in_use']: iface
                             for iface in ovs_db.ifaces if iface['mac_in_use']}

        self._bridges = {bridge['name']: self._bridge_attr(bridge)
                         for bridge in ovs_db.bridges}

    @property
    def bridges(self):
        return self._bridges

    def _bridge_attr(self, bridge_entry):
        stp = bridge_entry['stp_enable']
        ports = [self._ports_uuids[uuid] for uuid in bridge_entry['ports']]
        ports_info = {port['name']: self._port_attr(port)
                      for port in ports}

        return {'ports': ports_info, 'stp': stp}

    def _port_attr(self, port_entry):
        bond_info = (self._bond_info(port_entry) if self._is_bond(port_entry)
                     else None)
        tag = port_entry['tag']
        level = port_entry['other_config'].get('vdsm_level')

        return {'bond': bond_info, 'tag': tag, 'level': level}

    @staticmethod
    def _is_bond(port_entry):
        """
        Port in OVS DB does not contain explicit 'bond=True|False' entry. It is
        our responsibility to check whether a port is bond or not.
        """
        return len(port_entry['interfaces']) >= 2

    def _bond_info(self, port_entry):
        slaves = sorted([self._ifaces_uuids[uuid]['name']
                         for uuid in port_entry['interfaces']])
        active_slave = self._ifaces_macs.get(port_entry['bond_active_slave'])
        fake_iface = port_entry['bond_fake_iface']
        mode = port_entry['bond_mode']
        lacp = port_entry['lacp']

        return {'slaves': slaves, 'active_slave': active_slave,
                'fake_iface': fake_iface, 'mode': mode, 'lacp': lacp}

    @staticmethod
    def southbound_port(ports):
        return next((port for port, attrs in six.iteritems(ports)
                     if attrs['level'] == SOUTHBOUND), None)

    @staticmethod
    def northbound_ports(ports):
        return (port for port, attrs in six.iteritems(ports)
                if attrs['level'] == NORTHBOUND)

    @staticmethod
    def bonds(ports):
        return ((port, attrs['bond']) for port, attrs in six.iteritems(ports)
                if attrs['bond'])


def get_netinfo(ovs_info):
    addresses = getIpAddrs()
    routes = get_routes()

    _netinfo = {'networks': {}, 'bondings': {}}

    for bridge, bridge_attrs in six.iteritems(ovs_info.bridges):
        ports = bridge_attrs['ports']

        southbound = ovs_info.southbound_port(ports)

        # northbound ports represents networks
        stp = bridge_attrs['stp']
        for northbound_port in ovs_info.northbound_ports(ports):
            _netinfo['networks'][northbound_port] = _get_network_info(
                northbound_port, bridge, southbound, ports, stp, addresses,
                routes)

        for bond, bond_attrs in ovs_info.bonds(ports):
            _netinfo['bondings'][bond] = _get_bond_info(bond_attrs)

    return _netinfo


def _get_network_info(northbound, bridge, southbound, ports, stp, addresses,
                      routes):
    southbound_bond_attrs = ports[southbound]['bond']
    bond = southbound if southbound_bond_attrs else ''
    nics = (southbound_bond_attrs['slaves'] if southbound_bond_attrs
            else [southbound])
    tag = ports[northbound]['tag']
    network_info = {
        'iface': northbound,
        'bridged': True,
        'vlanid': tag,
        'bond': bond,
        'nics': nics,
        'ports': _get_net_ports(bridge, northbound, southbound, tag, ports),
        'stp': stp,
        'switch': 'ovs'
    }
    network_info.update(_get_iface_info(northbound, addresses, routes))
    return network_info


def _get_net_ports(bridge, northbound, southbound, net_tag, ports):
    if net_tag:
        net_southbound_port = '{}.{}'.format(southbound, net_tag)
    else:
        net_southbound_port = southbound

    net_ports = [net_southbound_port]
    net_ports += [port for port, port_attrs in six.iteritems(ports)
                  if (port_attrs['tag'] == net_tag and port != bridge and
                      port_attrs['level'] not in (SOUTHBOUND, NORTHBOUND))]

    return net_ports


def _get_bond_info(bond_attrs):
    bond_info = {
        'slaves': bond_attrs['slaves'],
        # TODO: what should we report when no slave is active?
        'active_slave': (bond_attrs['active_slave'] or
                         bond_attrs['slaves'][0]),
        'opts': _to_bond_opts(bond_attrs['mode'], bond_attrs['lacp']),
        'switch': 'ovs'
    }
    bond_info.update(EMPTY_PORT_INFO)
    return bond_info


def _to_bond_opts(mode, lacp):
    custom_opts = []
    if mode:
        custom_opts.append('ovs_mode:%s' % mode)
    if lacp:
        custom_opts.append('ovs_lacp:%s' % lacp)
    return {'custom': ','.join(custom_opts)} if custom_opts else {}


def _get_iface_info(iface, addresses, routes):
    ipv4gateway = get_gateway(routes, iface, family=4)
    ipv4addr, ipv4netmask, ipv4addrs, ipv6addrs = getIpInfo(
        iface, addresses, ipv4gateway)
    is_dhcpv4, is_dhcpv6 = dhcp_status(iface, addresses)

    return {'mtu': getMtu(iface), 'addr': ipv4addr, 'ipv4addrs': ipv4addrs,
            'gateway': ipv4gateway, 'netmask': ipv4netmask,
            'dhcpv4': is_dhcpv4, 'ipv6addrs': ipv6addrs,
            'ipv6gateway': get_gateway(routes, iface, family=6),
            'ipv6autoconf': is_ipv6_local_auto(iface), 'dhcpv6': is_dhcpv6}
