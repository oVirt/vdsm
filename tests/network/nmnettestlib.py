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

from contextlib import contextmanager

from vdsm.common.cmdutils import CommandPath
from vdsm.network import cmd
from vdsm.network.link import iface as linkiface
from vdsm.network.link.iface import random_iface_name

TEST_LINK_TYPE = 'bond'

SYSTEMCTL = CommandPath('systemctl', '/bin/systemctl', '/usr/bin/systemctl')

NM_SERVICE = 'NetworkManager'
NMCLI_BINARY = CommandPath('nmcli', '/usr/bin/nmcli')
IP_BINARY = CommandPath('ip', '/sbin/ip')


class NMService(object):
    def __init__(self):
        rc, out, err = cmd.exec_sync([SYSTEMCTL.cmd, 'status', NM_SERVICE])
        self.nm_init_state_is_up = rc == 0

    def setup(self):
        if not self.nm_init_state_is_up:
            cmd.exec_sync([SYSTEMCTL.cmd, 'start', NM_SERVICE])

    def teardown(self):
        if not self.nm_init_state_is_up:
            cmd.exec_sync([SYSTEMCTL.cmd, 'stop', NM_SERVICE])


def is_networkmanager_running():
    rc, _, _ = cmd.exec_sync([SYSTEMCTL.cmd, 'status', NM_SERVICE])
    return rc == 0


def iface_name():
    return random_iface_name('bond', max_length=11, digit_only=True)


@contextmanager
def nm_connections(
    bond_name,
    ipv4addr,
    slaves,
    connection_name=None,
    con_count=1,
    vlan=None,
    save=False,
):
    """
    Setting up a connection with an IP address, removing it at exit.
    In case connection_name is not provided, it will use the name of the iface.
    """
    if connection_name is None:
        connection_name = bond_name

    con_names = [connection_name + str(i) for i in range(con_count)]
    for con_name in con_names:
        _create_connection(
            con_name,
            bond_name,
            save,
            TEST_LINK_TYPE,
            ipv4addr=(ipv4addr if vlan is None else None),
        )

    # For the bond to be operationally up (carrier-up), add a slave
    _add_slaves_to_bond(bond=bond_name, slaves=slaves)

    if vlan is not None:
        vlan_iface = '.'.join([bond_name, vlan])
        _create_connection(
            vlan_iface,
            vlan_iface,
            save,
            'vlan',
            vlan_parent=bond_name,
            vlan_id=vlan,
            ipv4addr=ipv4addr,
        )

    try:
        yield con_names
    finally:
        if vlan is not None:
            _remove_connection(vlan_iface)
            _remove_device(vlan_iface)
        for con_name in con_names:
            _remove_connection(con_name)


def _create_connection(
    connection_name,
    iface_name,
    save,
    type,
    ipv4addr=None,
    vlan_parent=None,
    vlan_id=None,
):
    command = [
        NMCLI_BINARY.cmd,
        'con',
        'add',
        'con-name',
        connection_name,
        'ifname',
        iface_name,
        'save',
        'yes' if save else 'no',
        'type',
        type,
    ]
    if type == 'vlan' and vlan_parent and vlan_id is not None:
        command += ['vlan.id', vlan_id, 'dev', vlan_parent]
    if ipv4addr:
        command += ['ip4', ipv4addr]
    else:
        command += ['ipv4.method', 'disabled']

    _exec_cmd(command)


def _remove_connection(connection_name):
    command = [NMCLI_BINARY.cmd, 'con', 'del', connection_name]
    try:
        _exec_cmd(command)
    except NMCliError as ex:
        if 'Error: unknown connection' not in ex.args[1]:
            raise


def _remove_device(device_name):
    command = [NMCLI_BINARY.cmd, 'device', 'del', device_name]
    try:
        _exec_cmd(command)
    except NMCliError as ex:
        dev_not_found_msg = "Error: Device '{}' not found".format(device_name)
        if dev_not_found_msg not in ex.args[1]:
            raise


def _add_slaves_to_bond(bond, slaves):
    for slave in slaves:
        linkiface.iface(slave).down()
        command = [IP_BINARY.cmd, 'link', 'set', slave, 'master', bond]
        _exec_cmd(command)


def _exec_cmd(command):
    rc, out, err = cmd.exec_sync(command)

    if rc:
        raise NMCliError(rc, err)

    return out


class NMCliError(Exception):
    pass
