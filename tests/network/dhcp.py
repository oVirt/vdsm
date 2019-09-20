# Copyright 2013-2017 Red Hat, Inc.
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

from errno import ENOENT, ESRCH
import logging
import os
from signal import SIGKILL, SIGTERM
from subprocess import PIPE, Popen
from time import sleep, time

from vdsm.common.cmdutils import CommandPath
from vdsm.network import cmd

_DNSMASQ_BINARY = CommandPath('dnsmasq', '/usr/sbin/dnsmasq')
_DHCLIENT_BINARY = CommandPath(
    'dhclient', '/usr/sbin/dhclient', '/sbin/dhclient'
)
_START_CHECK_TIMEOUT = 0.5
_DHCLIENT_TIMEOUT = 10
_WAIT_FOR_STOP_TIMEOUT = 2
_DHCLIENT_LEASE = '/var/lib/dhclient/dhclient{0}--{1}.lease'
_DHCLIENT_LEASE_LEGACY = '/var/lib/dhclient/dhclient{0}-{1}.leases'


class DhcpError(Exception):
    pass


class Dnsmasq(object):
    def __init__(self):
        self._popen = None

    def start(
        self,
        interface,
        dhcp_range_from=None,
        dhcp_range_to=None,
        dhcpv6_range_from=None,
        dhcpv6_range_to=None,
        router=None,
        ipv6_slaac_prefix=None,
    ):
        # --dhcp-authoritative      The only DHCP server on network
        # -p 0                      don't act as a DNS server
        # --dhcp-option=3,<router>  advertise a specific gateway (or None)
        # --dhcp-option=6           don't reply with any DNS servers
        # -d                        don't daemonize and log to stderr
        # --bind-dynamic            bind only the testing veth iface
        command = [
            _DNSMASQ_BINARY.cmd,
            '--dhcp-authoritative',
            '-p',
            '0',
            '--dhcp-option=3' + (',{0}'.format(router) if router else ''),
            '--dhcp-option=6',
            '-i',
            interface,
            '-I',
            'lo',
            '-d',
            '--bind-dynamic',
        ]

        if dhcp_range_from and dhcp_range_to:
            command += [
                '--dhcp-range={0},{1},2m'.format(
                    dhcp_range_from, dhcp_range_to
                )
            ]
        if dhcpv6_range_from and dhcpv6_range_to:
            command += [
                '--dhcp-range={0},{1},2m'.format(
                    dhcpv6_range_from, dhcpv6_range_to
                )
            ]
        if ipv6_slaac_prefix:
            command += ['--enable-ra']
            command += ['--dhcp-range={0},slaac,2m'.format(ipv6_slaac_prefix)]

        self._popen = Popen(command, close_fds=True, stderr=PIPE)
        sleep(_START_CHECK_TIMEOUT)
        if self._popen.poll():
            raise DhcpError(
                'Failed to start dnsmasq DHCP server.\n%s\n%s'
                % (self._popen.stderr, ' '.join(command))
            )

    def stop(self):
        self._popen.kill()
        self._popen.wait()
        logging.debug(self._popen.stderr)


class ProcessCannotBeKilled(Exception):
    pass


class DhclientRunner(object):
    """On the interface, dhclient is run to obtain a DHCP lease.

    In the working directory (tmp_dir), which is managed by the caller.
    dhclient accepts the following date_formats: 'default' and 'local'.
    """

    def __init__(
        self, interface, family, tmp_dir, date_format, default_route=False
    ):
        self._interface = interface
        self._family = family
        self._date_format = date_format
        self._conf_file = os.path.join(tmp_dir, 'test.conf')
        self._pid_file = os.path.join(tmp_dir, 'test.pid')
        self.pid = None
        self.lease_file = os.path.join(tmp_dir, 'test.lease')
        cmds = [
            _DHCLIENT_BINARY.cmd,
            '-' + str(family),
            '-1',
            '-v',
            '-timeout',
            str(_DHCLIENT_TIMEOUT),
            '-cf',
            self._conf_file,
            '-pf',
            self._pid_file,
            '-lf',
            self.lease_file,
        ]
        if not default_route:
            # Instruct Fedora/EL's dhclient-script not to set gateway on iface
            cmds += ['-e', 'DEFROUTE=no']
        self._cmd = cmds + [self._interface]

    def _create_conf(self):
        with open(self._conf_file, 'w') as f:
            if self._date_format:
                f.write('db-time-format {0};'.format(self._date_format))

    def start(self):
        self._create_conf()
        rc, out, err = cmd.exec_sync(self._cmd)

        if rc:  # == 2
            logging.debug(err)
            raise DhcpError('dhclient failed to obtain a lease: %d', rc)

        with open(self._pid_file) as pid_file:
            self.pid = int(pid_file.readline())

    def stop(self):
        if self._try_kill(SIGTERM):
            return
        if self._try_kill(SIGKILL):
            return
        raise ProcessCannotBeKilled(
            'cmd=%s, pid=%s' % (' '.join(self._cmd), self.pid)
        )

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


def delete_dhclient_leases(iface, dhcpv4=False, dhcpv6=False):
    if dhcpv4:
        _delete_with_fallback(
            _DHCLIENT_LEASE.format('', iface),
            _DHCLIENT_LEASE_LEGACY.format('', iface),
        )
    if dhcpv6:
        _delete_with_fallback(
            _DHCLIENT_LEASE.format('6', iface),
            _DHCLIENT_LEASE_LEGACY.format('6', iface),
        )


def _delete_with_fallback(*file_names):
    """
    Delete the first file in file_names that exists.

    This is useful when removing dhclient lease files. dhclient stores leases
    either as e.g. 'dhclient6-test-network.leases' if it existed before, or as
    'dhclient6--test-network.lease'. The latter is more likely to exist.

    We intentionally only delete one file, the one initscripts chose and wrote
    to. Since the legacy one is preferred, after the test it will be gone and
    on the next run ifup will use the more modern name.
    """
    for name in file_names:
        try:
            os.unlink(name)
            return
        except OSError as ose:
            if ose.errno != ENOENT:
                logging.error('Failed to delete: %s', name, exc_info=True)
                raise
