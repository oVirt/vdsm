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
from signal import SIGKILL, SIGTERM
from time import sleep, time
from errno import ENOENT, ESRCH

from nose.plugins.skip import SkipTest

from vdsm.utils import CommandPath
from vdsm.utils import execCmd

_DNSMASQ_BINARY = CommandPath('dnsmasq', '/usr/sbin/dnsmasq')
_DHCLIENT_BINARY = CommandPath('dhclient', '/usr/sbin/dhclient',
                               '/sbin/dhclient')
_NM_CLI_BINARY = CommandPath('nmcli', '/usr/bin/nmcli')
_START_CHECK_TIMEOUT = 0.5
_DHCLIENT_TIMEOUT = 10
_WAIT_FOR_STOP_TIMEOUT = 2


class DhcpError(Exception):
    pass


class Dnsmasq():
    def __init__(self):
        self.proc = None

    def start(self, interface, dhcp_range_from, dhcp_range_to,
              dhcpv6_range_from=None, dhcpv6_range_to=None, router=None,
              bind_dynamic=True):
        # -p 0                      don't act as a DNS server
        # --dhcp-option=3,<router>  advertise a specific gateway (or None)
        # --dhcp-option=6           don't reply with any DNS servers
        # -d                        don't daemonize and log to stderr
        # --bind-dynamic            bind only the testing veth iface
        # (a better, and quiet, version of --bind-interfaces, but not on EL6)
        self.proc = execCmd([
            _DNSMASQ_BINARY.cmd, '--dhcp-authoritative',
            '-p', '0',
            '--dhcp-range={0},{1},2m'.format(dhcp_range_from, dhcp_range_to),
            '--dhcp-option=3' + (',{0}'.format(router) if router else ''),
            '--dhcp-option=6',
            '-i', interface, '-I', 'lo', '-d',
            '--bind-dynamic' if bind_dynamic else '--bind-interfaces']
            + (['--dhcp-range={0},{1},2m'.format(dhcpv6_range_from,
                                                 dhcpv6_range_to)]
               if dhcpv6_range_from and dhcpv6_range_to else []), sync=False)
        sleep(_START_CHECK_TIMEOUT)
        if self.proc.returncode:
            raise DhcpError('Failed to start dnsmasq DHCP server.' +
                            ''.join(self.proc.stderr))

    def stop(self):
        self.proc.kill()
        logging.debug(''.join(self.proc.stderr))


class ProcessCannotBeKilled(Exception):
    pass


class DhclientRunner(object):
    """On the interface, dhclient is run to obtain a DHCP lease.

    In the working directory (tmp_dir), which is managed by the caller.
    dhclient accepts the following date_formats: 'default' and 'local'.
    """
    def __init__(self, interface, family, tmp_dir, date_format,
                 default_route=False):
        self._interface = interface
        self._family = family
        self._date_format = date_format
        self._conf_file = os.path.join(tmp_dir, 'test.conf')
        self._pid_file = os.path.join(tmp_dir, 'test.pid')
        self.pid = None
        self.lease_file = os.path.join(tmp_dir, 'test.lease')
        cmd = [_DHCLIENT_BINARY.cmd, '-' + str(family), '-1', '-v',
               '-timeout', str(_DHCLIENT_TIMEOUT), '-cf', self._conf_file,
               '-pf', self._pid_file, '-lf', self.lease_file
               ]
        if not default_route:
            # Instruct Fedora/EL's dhclient-script not to set gateway on iface
            cmd += ['-e', 'DEFROUTE=no']
        self._cmd = cmd + [self._interface]

    def _create_conf(self):
        with open(self._conf_file, 'w') as f:
            f.write('db-time-format {0};'.format(self._date_format))

    def start(self):
        self._create_conf()
        rc, out, err = execCmd(self._cmd)

        if rc:  # == 2
            logging.debug(''.join(err))
            raise DhcpError('dhclient failed to obtain a lease: %d', rc)

        with open(self._pid_file) as pid_file:
            self.pid = int(pid_file.readline())

    def stop(self):
        if self._try_kill(SIGTERM):
            return
        if self._try_kill(SIGKILL):
            return
        raise ProcessCannotBeKilled('cmd=%s, pid=%s' % (' '.join(self._cmd),
                                                        self.pid))

    def _try_kill(self, signal, timeout=_WAIT_FOR_STOP_TIMEOUT):
        now = time()
        deadline = now + timeout
        while now < deadline:
            try:
                os.kill(self.pid, signal)
            except OSError as err:
                if err.errno != ESRCH:
                    raise
                return True  # no such process

            sleep(0.5)
            if not self._is_running():
                return True
            now = time()

        return False

    def _is_running(self):
        executable_link = '/proc/{0}/exe'.format(self.pid)
        try:
            executable = os.readlink(executable_link)
        except OSError as err:
            if err.errno == ENOENT:
                return False  # no such pid
            else:
                raise
        return executable == _DHCLIENT_BINARY.cmd


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
