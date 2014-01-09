# Copyright 2013-2014 Red Hat, Inc.
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

import logging
from nose.plugins.skip import SkipTest

from vdsm.utils import CommandPath
from vdsm.utils import execCmd

from time import sleep

_DNSMASQ_BINARY = CommandPath('dnsmasq', '/usr/sbin/dnsmasq')
_DHCLIENT_BINARY = CommandPath('dhclient', '/usr/sbin/dhclient')
_NM_CLI_BINARY = CommandPath('nmcli', '/usr/bin/nmcli')
_START_CHECK_TIMEOUT = 0.5
_DHCLIENT_TIMEOUT = 10


class DhcpError(Exception):
    pass


class Dnsmasq():
    def __init__(self):
        self.proc = None

    def start(self, interface, dhcpRangeFrom, dhcpRangeTo):
        # --dhcp-option=3 don't send gateway address which would break routing
        # -k              do not daemonize
        # -p 0            disable all the dnsmasq dns functionality
        self.proc = execCmd([_DNSMASQ_BINARY.cmd, '--dhcp-authoritative',
                            '-p', '0', '--dhcp-range=' + dhcpRangeFrom + ',' +
                            dhcpRangeTo + ',2m', '--dhcp-option=3', '-k',
                            '-i', interface, '-I', 'lo', '-d',
                            '--bind-dynamic'], sync=False)
        sleep(_START_CHECK_TIMEOUT)
        if self.proc.returncode:
            raise DhcpError('Failed to start dnsmasq DHCP server.' +
                            ''.join(self.proc.stderr))

    def stop(self):
        self.proc.kill()
        logging.debug(''.join(self.proc.stderr))


def runDhclient(interface, leaseFile, pidFile):
    """Starts dhclient and hands the process over after a while."""
    rc, out, err = execCmd([_DHCLIENT_BINARY.cmd, '-d', '-lf', leaseFile,
                            '-pf', pidFile, '-timeout', str(_DHCLIENT_TIMEOUT),
                            '-1', interface])

    if rc:  # == 2
        logging.debug(''.join(err))
        raise DhcpError('dhclient failed to obtain a lease: %d', rc)


def addNMplaceholderConnection(interface, connection):
    """Creating our own 'connection' with a static address prevents Network
    Manager from running dhclient on the interface.

    And so it does not interfere with dhclient we are going to run."""
    rc, out, err = execCmd([_NM_CLI_BINARY.cmd, 'connection', 'add',
                            'type', 'ethernet', 'ifname', interface,
                            'con-name', connection, 'autoconnect', 'yes',
                            'ip4', '12.34.56.78'])

    if rc:
        raise SkipTest('Could not add a placeholder NM connection.')


def removeNMplaceholderConnection(connection):
    rc, out, err = execCmd([_NM_CLI_BINARY.cmd, 'connection',
                            'delete', connection])

    if rc:
        raise DhcpError('Could not remove the placeholder NM connection.')
