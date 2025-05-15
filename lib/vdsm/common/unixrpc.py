# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

import base64
import http.client
import socket
import xmlrpc.client


class UnixXmlRpcClient(xmlrpc.client.ServerProxy):
    """
    This class implements a XML-RPC client that connects to a UNIX socket. The
    path to the UNIX socket to create must be provided.
    """
    def __init__(self, sock_path, timeout):
        # We can't pass funny characters in the host part of a URL, so we
        # encode the socket path in base16.
        uri = base64.b16encode(sock_path.encode('utf-8')).decode('utf-8')
        xmlrpc.client.ServerProxy.__init__(
            self,
            'http://' + uri,
            transport=UnixXmlRpcTransport(timeout),
            allow_none=1)


class UnixXmlRpcTransport(xmlrpc.client.Transport):
    def __init__(self, timeout):
        xmlrpc.client.Transport.__init__(self)
        self.timeout = timeout

    def make_connection(self, host):
        return UnixXmlRpcHttpConnection(host, timeout=self.timeout)


class UnixXmlRpcHttpConnection(http.client.HTTPConnection):
    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(base64.b16decode(self.host))
