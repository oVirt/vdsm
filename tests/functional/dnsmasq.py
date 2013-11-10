# Copyright 2013 Red Hat, Inc.
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

from vdsm.utils import CommandPath
from vdsm.utils import execCmd

from time import sleep

_DNSMASQ_BINARY = CommandPath('dnsmasq', '/usr/sbin/dnsmasq')
_START_CHECK_TIMEOUT = 0.5


class DnsmasqError(Exception):
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
                            dhcpRangeTo, '--dhcp-option=3', '-k', '-i',
                            interface], sync=False)
        sleep(_START_CHECK_TIMEOUT)
        if self.proc.returncode:
            raise DnsmasqError('Failed to start dnsmasq DHCP server.')

    def stop(self):
        self.proc.kill()
