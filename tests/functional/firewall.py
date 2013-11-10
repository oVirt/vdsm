#
# Copyright 2013 Red Hat, Inc.
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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#

from nose.plugins.skip import SkipTest

from vdsm.utils import CommandPath
from vdsm.utils import execCmd

_FIREWALLD_BINARY = CommandPath('firewall-cmd', '/bin/firewall-cmd')
_IPTABLES_BINARY = CommandPath('iptables', '/sbin/iptables')
_SERVICE_BINARY = CommandPath('service', '/sbin/service')


class FirewallError(Exception):
    pass


def allowDhcp(interface):
    try:
        if _serviceRunning('iptables'):
            _execCmdChecker([_IPTABLES_BINARY.cmd, '-I', 'INPUT', '-i',
                            interface, '-p', 'udp', '--sport', '68', '--dport',
                            '67', '-j', 'ACCEPT'])
        if _serviceRunning('firewalld'):
            """
            zone "work" is used to not to disable dhcp in the public zone
            after the test finishes, if it is enabled there
            """
            _execCmdChecker([_FIREWALLD_BINARY.cmd, '--zone=work',
                            '--add-interface=' + interface])
            _execCmdChecker([_FIREWALLD_BINARY.cmd, '--zone=work',
                            '--add-service=dhcp'])
    except FirewallError as e:
        raise SkipTest('Failed to allow dhcp traffic in firewall because of '
                       '%s' % e)


def stopAllowingDhcp(interface):
    try:
        if _serviceRunning('iptables'):
            _execCmdChecker([_IPTABLES_BINARY.cmd, '-D', 'INPUT', '-i',
                            interface, '-p', 'udp', '--sport', '68', '--dport',
                            '67', '-j', 'ACCEPT'])
        if _serviceRunning('firewalld'):
            _execCmdChecker([_FIREWALLD_BINARY.cmd, '--zone=work',
                            '--remove-service=dhcp'])
            _execCmdChecker([_FIREWALLD_BINARY.cmd, '--zone=work',
                            '--remove-interface=' + interface])
    except FirewallError as e:
        raise SkipTest('Failed to remove created rules from firewall because '
                       'of %s' % e)


def _serviceRunning(name):
    ret, _, _ = execCmd([_SERVICE_BINARY.cmd, name, 'status'])
    # return code 0 means service is running
    return not ret


def _execCmdChecker(command):
    ret, _, err = execCmd(command)
    if ret:
        raise FirewallError('Command %s failed with %s' % (command[0], err))
