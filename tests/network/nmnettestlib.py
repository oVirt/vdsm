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

from contextlib import contextmanager

from vdsm.commands import execCmd
from vdsm.network.link import iface as linkiface
from vdsm.utils import CommandPath

from .nettestlib import dummy_device, random_iface_name

TEST_LINK_TYPE = 'bond'

SYSTEMCTL = CommandPath('systemctl', '/bin/systemctl', '/usr/bin/systemctl')

NM_SERVICE = 'NetworkManager'
NMCLI_BINARY = CommandPath('nmcli', '/usr/bin/nmcli')
IP_BINARY = CommandPath('ip', '/sbin/ip')


class NMService(object):
    def __init__(self):
        rc, out, err = execCmd([SYSTEMCTL.cmd, 'status', NM_SERVICE])
        self.nm_init_state_is_up = (rc == 0)

    def setup(self):
        if not self.nm_init_state_is_up:
            execCmd([SYSTEMCTL.cmd, 'start', NM_SERVICE])

    def teardown(self):
        if not self.nm_init_state_is_up:
            execCmd([SYSTEMCTL.cmd, 'stop', NM_SERVICE])


def is_networkmanager_running():
    rc, _, _ = execCmd([SYSTEMCTL.cmd, 'status', NM_SERVICE])
    return rc == 0


def iface_name():
    return random_iface_name('bond', max_length=11, digit_only=True)


@contextmanager
def nm_connections(iface_name, ipv4addr, connection_name=None, con_count=1,
                   save=False):
    """
    Setting up a connection with an IP address, removing it at exit.
    In case connection_name is not provided, it will use the name of the iface.
    """
    if connection_name is None:
        connection_name = iface_name

    with dummy_device() as slave:
        for i in range(con_count):
            _create_connection(
                connection_name + str(i), iface_name, ipv4addr, save)

        # For the bond to be operationally up (carrier-up), add a slave
        _add_slave_to_bond(bond=iface_name, slave=slave)

        try:
            yield
        finally:
            for i in range(con_count):
                _remove_connection(connection_name + str(i))


def _create_connection(connection_name, iface_name, ipv4addr, save):
    command = [NMCLI_BINARY.cmd, 'con', 'add', 'con-name', connection_name,
               'ifname', iface_name, 'save', 'yes' if save else 'no',
               'type', TEST_LINK_TYPE, 'ip4', ipv4addr]
    _exec_cmd(command)


def _remove_connection(connection_name):
    command = [NMCLI_BINARY.cmd, 'con', 'del', connection_name]
    try:
        _exec_cmd(command)
    except NMCliError as ex:
        if 'Error: unknown connection' not in ex.args[1]:
            raise


def _add_slave_to_bond(bond, slave):
    linkiface.down(slave)
    command = [IP_BINARY.cmd, 'link', 'set', slave, 'master', bond]
    _exec_cmd(command)


def _exec_cmd(command):
    rc, out, err = execCmd(command)

    if rc:
        raise NMCliError(rc, ' '.join(err))

    return out


class NMCliError(Exception):
    pass
