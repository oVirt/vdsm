#
# Copyright 2013-2014 Red Hat, Inc.
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

import logging
from nose.plugins.skip import SkipTest

from vdsm.utils import CommandPath
from vdsm.utils import execCmd

_FIREWALLD_BINARY = CommandPath('firewall-cmd', '/bin/firewall-cmd')
_IPTABLES_BINARY = CommandPath('iptables', '/sbin/iptables')
_SERVICE_BINARY = CommandPath('service', '/sbin/service')


class FirewallError(Exception):
    pass


def allowDhcp(veth):
    """Allows DHCP traffic on a testing veth interface.

    When using the iptables service, no other traffic is allowed.
    With firewalld, the whole interface is moved to the 'trusted',
    unrestricted zone.
    """
    try:
        if _serviceRunning('iptables'):
            _execCmdChecker([_IPTABLES_BINARY.cmd, '-I', 'INPUT', '-i',
                            veth, '-p', 'udp', '--sport', '68', '--dport',
                            '67', '-j', 'ACCEPT'])
        elif _serviceRunning('firewalld'):
            _execCmdChecker([_FIREWALLD_BINARY.cmd, '--zone=trusted',
                            '--change-interface=' + veth])
        else:
            logging.info('No firewall service detected.')
    except FirewallError as e:
        raise SkipTest('Failed to allow DHCP traffic in firewall: %s' % e)


def stopAllowingDhcp(veth):
    """Removes the rules allowing DHCP on the testing veth interface.

    As the interface is expected to be removed from the system,
    this function merely reverses the effect of the 'allowDhcp' function
    just to clean up.
    For iptables, it deletes the rule introduced. For firewalld, it removes
    the interface from the 'trusted' zone.

    If cleaning up fails the affected test must fail too (with FirewallError).
    """
    if _serviceRunning('iptables'):
        _execCmdChecker([_IPTABLES_BINARY.cmd, '-D', 'INPUT', '-i',
                        veth, '-p', 'udp', '--sport', '68', '--dport',
                        '67', '-j', 'ACCEPT'])
    elif _serviceRunning('firewalld'):
        _execCmdChecker([_FIREWALLD_BINARY.cmd, '--zone=trusted',
                        '--remove-interface=' + veth])
    else:
        logging.warning('No firewall service detected.')


def _serviceRunning(name):
    ret, _, _ = execCmd([_SERVICE_BINARY.cmd, name, 'status'])
    # return code 0 means service is running
    return not ret


def _execCmdChecker(command):
    ret, out, err = execCmd(command)
    if ret:
        raise FirewallError('Command {0} failed with {1}; {2}'.format(
                            command, out, err))
