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
import os
from time import sleep

from nose.plugins.skip import SkipTest

from vdsm.utils import CommandPath
from vdsm.utils import execCmd

_DNSMASQ_BINARY = CommandPath('dnsmasq', '/usr/sbin/dnsmasq')
_DHCLIENT_BINARY = CommandPath('dhclient', '/usr/sbin/dhclient',
                               '/sbin/dhclient')
_NM_CLI_BINARY = CommandPath('nmcli', '/usr/bin/nmcli')
_START_CHECK_TIMEOUT = 0.5
_DHCLIENT_TIMEOUT = 10


class DhcpError(Exception):
    pass


class Dnsmasq():
    def __init__(self):
        self.proc = None

    def start(self, interface, dhcpRangeFrom, dhcpRangeTo, router=None):
        # --dhcp-option=3,<router> advertise specific router (can be None)
        # -O 6            don't reply with any DNS servers either
        # -d              do not daemonize and log to stderr
        # -p 0            disable all the dnsmasq dns functionality
        self.proc = execCmd([
            _DNSMASQ_BINARY.cmd, '--dhcp-authoritative',
            '-p', '0', '--dhcp-range=' + dhcpRangeFrom + ',' +
            dhcpRangeTo + ',2m',
            '--dhcp-option=3' + ',%s' % (router,) if router else '',
            '-O', '6',
            '-i', interface, '-I', 'lo', '-d',
            '--bind-interfaces'], sync=False)
        sleep(_START_CHECK_TIMEOUT)
        if self.proc.returncode:
            raise DhcpError('Failed to start dnsmasq DHCP server.' +
                            ''.join(self.proc.stderr))

    def stop(self):
        self.proc.kill()
        logging.debug(''.join(self.proc.stderr))


def runDhclient(interface, tmpDir, dateFormat):
    """On the interface, dhclient is run to obtain a DHCP lease.

    In the working directory (tmpDir), which is managed by the caller,
    a lease file is created and a path to it is returned.
    dhclient accepts the following dateFormats: 'default' and 'local'.
    """
    confFile = os.path.join(tmpDir, 'test.conf')
    pidFile = os.path.join(tmpDir, 'test.pid')
    leaseFile = os.path.join(tmpDir, 'test.lease')

    with open(confFile, 'w') as f:
        f.write('db-time-format {0};'.format(dateFormat))

    rc, out, err = execCmd([_DHCLIENT_BINARY.cmd, '-lf', leaseFile,
                            '-pf', pidFile, '-timeout', str(_DHCLIENT_TIMEOUT),
                            '-1', '-cf', confFile, interface])

    if rc:  # == 2
        logging.debug(''.join(err))
        raise DhcpError('dhclient failed to obtain a lease: %d', rc)

    return leaseFile


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
