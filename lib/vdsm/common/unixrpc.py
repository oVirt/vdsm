#
# Copyright 2018 Red Hat, Inc.
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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license

from __future__ import absolute_import
from __future__ import division

import base64
import socket

from six.moves import http_client
from six.moves import xmlrpc_client


class UnixXmlRpcClient(xmlrpc_client.ServerProxy):
    """
    This class implements a XML-RPC client that connects to a UNIX socket. The
    path to the UNIX socket to create must be provided.
    """
    def __init__(self, sock_path, timeout):
        # We can't pass funny characters in the host part of a URL, so we
        # encode the socket path in base16.
        uri = base64.b16encode(sock_path.encode('utf-8')).decode('utf-8')
        xmlrpc_client.ServerProxy.__init__(
            self,
            'http://' + uri,
            transport=UnixXmlRpcTransport(timeout),
            allow_none=1)


class UnixXmlRpcTransport(xmlrpc_client.Transport):
    def __init__(self, timeout):
        xmlrpc_client.Transport.__init__(self)
        self.timeout = timeout

    def make_connection(self, host):
        return UnixXmlRpcHttpConnection(host, timeout=self.timeout)


class UnixXmlRpcHttpConnection(http_client.HTTPConnection):
    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(base64.b16decode(self.host))
