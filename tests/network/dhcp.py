# Copyright 2013-2020 Red Hat, Inc.
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

import logging
from subprocess import PIPE, Popen
from time import sleep

from vdsm.common.cmdutils import CommandPath

_DNSMASQ_BINARY = CommandPath('dnsmasq', '/usr/sbin/dnsmasq')
_START_CHECK_TIMEOUT = 0.5


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
        # --conf-file=/dev/null     Start with empty config, el8 has already
        #                           empty default config, but el9 has
        #                           bind-interfaces which conflicts with
        #                           bind-dynamic.
        # --dhcp-authoritative      The only DHCP server on network
        # -p 0                      don't act as a DNS server
        # --dhcp-option=3,<router>  advertise a specific gateway (or None)
        # --dhcp-option=6           don't reply with any DNS servers
        # -d                        don't daemonize and log to stderr
        # --bind-dynamic            bind only the testing veth iface
        # --no-ping                 skip dhcpv4 check if the address is
        #                           available
        command = [
            _DNSMASQ_BINARY.cmd,
            '--conf-file=/dev/null',
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
            '--no-ping',
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

        self._popen = Popen(
            command, close_fds=True, stderr=PIPE, encoding='utf-8'
        )
        sleep(_START_CHECK_TIMEOUT)
        if self._popen.poll():
            raise DhcpError(
                'Failed to start dnsmasq DHCP server.\n%s\n%s'
                % (self._popen.stderr.read(), ' '.join(command))
            )

    def stop(self):
        self._popen.kill()
        self._popen.wait()
        logging.debug(self._popen.stderr.read())
