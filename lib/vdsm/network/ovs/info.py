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

from . import driver


NORTHBOUND = 'northbound'
SOUTHBOUND = 'southbound'


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
