# Copyright 2019 Red Hat, Inc.
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


MIN_PORT = 0
MAX_PORT = 65535

# This code is based on imageio -
# https://github.com/oVirt/ovirt-imageio/blob/
# e2fd416f026eee3b7b4acd4fc7c867ceb7ab87f1/common/
# ovirt_imageio_common/nbd.py#L138


class UnixAddress(str):
    """
    Unix socket address representation
    """
    @property
    def transport(self):
        return "unix"

    @property
    def path(self):
        return str(self)

    def url(self, export=None):
        s = "nbd:unix:{}".format(self.path)
        if export:
            s += ":exportname=" + export
        return s


class TCPAddress(tuple):
    """
    TCP address representation
    """
    def __new__(cls, host, port):
        if port < MIN_PORT or port > MAX_PORT:
            raise ValueError(
                'Port {} out is valid range {}-{}'.format(
                    port, MIN_PORT, MAX_PORT))
        return tuple.__new__(cls, (host, port))

    @property
    def transport(self):
        return "tcp"

    @property
    def host(self):
        return self[0]

    @property
    def port(self):
        return self[1]

    def url(self, export=None):
        s = "nbd:{}:{}".format(self.host, self.port)
        if export:
            s += ":exportname=" + export
        return s
