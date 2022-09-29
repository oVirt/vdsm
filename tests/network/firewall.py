# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

from contextlib import contextmanager
import logging

from vdsm.common.cmdutils import CommandPath
from vdsm.network import cmd

_FIREWALLD_BINARY = CommandPath('firewall-cmd', '/bin/firewall-cmd')
_IPTABLES_BINARY = CommandPath('iptables', '/sbin/iptables')
_SYSTEMCTL_BINARY = CommandPath('systemctl', '/bin/systemctl')


class FirewallError(Exception):
    pass


@contextmanager
def allow_dhcp(iface):
    """Temporarily allow DHCP traffic in firewall."""
    _allow_dhcp(iface)
    try:
        yield
    finally:
        _forbid_dhcp(iface)


def _allow_dhcp(iface):
    """Allow DHCP traffic on an interface.

    When using the iptables service, no other traffic is allowed.
    With firewalld, the whole interface is moved to the 'trusted',
    unrestricted zone.
    """
    if _serviceRunning('iptables'):
        _exec_cmd_checker(
            [
                _IPTABLES_BINARY.cmd,
                '--wait',
                '-I',
                'INPUT',
                '-i',
                iface,
                '-p',
                'udp',
                '--sport',
                '68',
                '--dport',
                '67',
                '-j',
                'ACCEPT',
            ]
        )  # DHCPv4
        _exec_cmd_checker(
            [
                _IPTABLES_BINARY.cmd,
                '--wait',
                '-I',
                'INPUT',
                '-i',
                iface,
                '-p',
                'udp',
                '--sport',
                '546',
                '--dport',
                '547',
                '-j',
                'ACCEPT',
            ]
        )  # DHCPv6
    elif _serviceRunning('firewalld'):
        _exec_cmd_checker(
            [
                _FIREWALLD_BINARY.cmd,
                '--zone=trusted',
                '--change-interface=' + iface,
            ]
        )
    else:
        logging.info('No firewall service detected.')


def _forbid_dhcp(iface):
    """Remove the rules allowing DHCP on the interface.

    As the interface is expected to be removed from the system, this function
    merely reverses the effect of _allow_dhcp(), just to clean up.
    For iptables, it deletes the rule introduced. For firewalld, it removes
    the interface from the 'trusted' zone.

    If cleaning up fails the affected test must fail too (with FirewallError).
    """
    if _serviceRunning('iptables'):
        _exec_cmd_checker(
            [
                _IPTABLES_BINARY.cmd,
                '--wait',
                '-D',
                'INPUT',
                '-i',
                iface,
                '-p',
                'udp',
                '--sport',
                '68',
                '--dport',
                '67',
                '-j',
                'ACCEPT',
            ]
        )  # DHCPv4
        _exec_cmd_checker(
            [
                _IPTABLES_BINARY.cmd,
                '--wait',
                '-D',
                'INPUT',
                '-i',
                iface,
                '-p',
                'udp',
                '--sport',
                '546',
                '--dport',
                '547',
                '-j',
                'ACCEPT',
            ]
        )  # DHCPv6
    elif _serviceRunning('firewalld'):
        _exec_cmd_checker(
            [
                _FIREWALLD_BINARY.cmd,
                '--zone=trusted',
                '--remove-interface=' + iface,
            ]
        )
    else:
        logging.warning('No firewall service detected.')


def _serviceRunning(name):
    ret, _, _ = cmd.exec_sync([_SYSTEMCTL_BINARY.cmd, name, 'status'])
    # return code 0 means service is running
    return not ret


def _exec_cmd_checker(command):
    ret, out, err = cmd.exec_sync(command)
    if ret:
        raise FirewallError(
            'Command {0} failed with {1}; {2}'.format(command, out, err)
        )
