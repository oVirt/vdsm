#
# Copyright 2008-2011 Red Hat, Inc.
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

"""xmlrpclib with a keep-alive transport.

Throws a timeout exception to the client when the underlying
TCP transport is broken.

Inspired by Volodymyr Orlenko,
http://blog.bjola.ca/2007/08/using-timeout-with-xmlrpclib.html

Sets up an xmlrpc Server with a modified Transport
(TcpkeepTransport) which uses a (slightly modified) HTTP
protocol (TcpkeepHTTP) that uses TcpkeepHTTPConnection when it
needs to set up a connection.
"""

import xmlrpclib, httplib
import socket

# It would have been nicer to make these server-specific and not module-wide
# constants. But it is not really importat for it, so it should wait.
KEEPIDLE = 60
KEEPINTVL = 10
KEEPCNT = 6

CONNECTTIMEOUT = 160

def Server(url, *args, **kwargs):
    kwargs['transport'] = TcpkeepTransport()
    server = xmlrpclib.Server(url, *args, **kwargs)
    return server

ServerProxy = Server

class TcpkeepTransport(xmlrpclib.Transport):

    def make_connection(self, host):
        if hasattr(xmlrpclib.Transport, "single_request"): # Python 2.7
            return TcpkeepHTTPConnection(host)
        else:
            return TcpkeepHTTP(host)

class TcpkeepHTTPConnection(httplib.HTTPConnection):
    def connect(self):
        """Connect to the host and port specified in __init__.

        taken from httplib.HTTPConnection.connect(), with few additions for
        connection timeout and keep-alive

        after TCP_KEEPIDLE seconds of silence, TCP_KEEPCNT probes would be
        sent, TCP_KEEPINTVL seconds apart of each other. If all of them
        fail, the socket is closed."""

        msg = "getaddrinfo returns an empty list"
        for res in socket.getaddrinfo(self.host, self.port, 0,
                                      socket.SOCK_STREAM):
            af, socktype, proto, canonname, sa = res
            try:
                self.sock = socket.socket(af, socktype, proto)
                if self.debuglevel > 0:
                    print "connect: (%s, %s)" % (self.host, self.port)

                oldtimeout = self.sock.gettimeout()  # added
                self.sock.settimeout(CONNECTTIMEOUT) # added
                self.sock.connect(sa)
                self.sock.settimeout(oldtimeout)     # added
            except socket.error, msg:
                if self.debuglevel > 0:
                    print 'connect fail:', (self.host, self.port)
                if self.sock:
                    self.sock.close()
                self.sock = None
                continue
            break
        if not self.sock:
            raise socket.error, msg
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)       # added
        self.sock.setsockopt(socket.SOL_TCP, socket.TCP_KEEPIDLE, KEEPIDLE)   # added
        self.sock.setsockopt(socket.SOL_TCP, socket.TCP_KEEPINTVL, KEEPINTVL) # added
        self.sock.setsockopt(socket.SOL_TCP, socket.TCP_KEEPCNT, KEEPCNT)     # added

class TcpkeepHTTP(httplib.HTTP):
    _connection_class = TcpkeepHTTPConnection

###################
# the same, for ssl
from vdsm import SecureXMLRPCServer
import ssl

def SslServer(url, ctx, *args, **kwargs):
    kwargs['transport'] = TcpkeepSafeTransport(ctx)
    server = xmlrpclib.Server(url, *args, **kwargs)
    return server

SslServerProxy = SslServer

class TcpkeepSafeTransport(SecureXMLRPCServer.VerifyingSafeTransport):

    def make_connection(self, host):
        chost, self._extra_headers, x509 = self.get_host_info(host)
        if hasattr(xmlrpclib.SafeTransport, "single_request"): # Python 2.7
            return TcpkeepHTTPSConnection(
                        chost, None, key_file=self.key_file, strict=None,
                        timeout=CONNECTTIMEOUT,
                        cert_file=self.cert_file, ca_certs=self.ca_certs,
                        cert_reqs=self.cert_reqs)
        else:
            return TcpkeepHTTPS(
                        chost, None, key_file=self.key_file,
                        cert_file=self.cert_file, ca_certs=self.ca_certs,
                        cert_reqs=self.cert_reqs)


class TcpkeepHTTPSConnection(SecureXMLRPCServer.VerifyingHTTPSConnection):
    def __init__(self, host, port=None, key_file=None, cert_file=None,
                 strict=None, timeout=CONNECTTIMEOUT,
                 ca_certs=None, cert_reqs=ssl.CERT_REQUIRED):
        SecureXMLRPCServer.VerifyingHTTPSConnection.__init__(
                 self, host, port=port, key_file=key_file, cert_file=cert_file,
                 strict=strict, timeout=timeout,
                 ca_certs=ca_certs, cert_reqs=cert_reqs)

    def connect(self):
        SecureXMLRPCServer.VerifyingHTTPSConnection.connect(self)

        # after TCP_KEEPIDLE seconds of silence, TCP_KEEPCNT probes would be
        # sent, TCP_KEEPINTVL seconds apart of each other. If all of them fail,
        # the socket is closed.
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        self.sock.setsockopt(socket.SOL_TCP, socket.TCP_KEEPIDLE, KEEPIDLE)
        self.sock.setsockopt(socket.SOL_TCP, socket.TCP_KEEPINTVL, KEEPINTVL)
        self.sock.setsockopt(socket.SOL_TCP, socket.TCP_KEEPCNT, KEEPCNT)


class TcpkeepHTTPS(SecureXMLRPCServer.VerifyingHTTPS):
    _connection_class = TcpkeepHTTPSConnection

