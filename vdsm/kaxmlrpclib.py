"""xmlrpclib with a keep-alive transport.

Throws a timeout exception to the client when the underlying
TCP transport is broken.

Copyright 2008 Red Hat, Inc. and/or its affiliates.

Licensed to you under the GNU General Public License as published by
the Free Software Foundation; either version 2 of the License, or
(at your option) any later version.  See the files README and
LICENSE_GPL_v2 which accompany this distribution.

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
        conn = TcpkeepHTTP(host)
        return conn

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
from M2Crypto import m2xmlrpclib, httpslib as m2httpslib, SSL as m2SSL, m2urllib

def SslServer(url, ctx, *args, **kwargs):
    kwargs['transport'] = TcpkeepSafeTransport(ctx)
    server = m2xmlrpclib.Server(url, *args, **kwargs)
    return server

SslServerProxy = SslServer

class TcpkeepSafeTransport(m2xmlrpclib.SSL_Transport):

    def make_connection(self, host, port, ssl_context):
        conn = TcpkeepHTTPS(host, port, ssl_context=ssl_context)
        return conn

    # sadly, m2crypto's SSL_Transport does not even have make_connection()
    # so I have to copy the whole request() from M2Crypto/m2xmlrpclib.py
    def request(self, host, handler, request_body, verbose=0):
        # Handle username and password.
        user_passwd, host_port = m2urllib.splituser(host)
        _host, _port = m2urllib.splitport(host_port)
#        h = httpslib.HTTPS(_host, int(_port), ssl_context=self.ssl_ctx) danken
        h = self.make_connection(_host, int(_port), ssl_context=self.ssl_ctx)
        if verbose:
            h.set_debuglevel(1)

        # What follows is as in xmlrpclib.Transport. (Except the authz bit.)
        h.putrequest("POST", handler)

        # required by HTTP/1.1
        h.putheader("Host", _host)

        # required by XML-RPC
        h.putheader("User-Agent", self.user_agent)
        h.putheader("Content-Type", "text/xml")
        h.putheader("Content-Length", str(len(request_body)))

        # Authorisation.
        if user_passwd is not None:
            import string, base64
            auth=string.strip(base64.encodestring(user_passwd))
            h.putheader('Authorization', 'Basic %s' % auth)

        h.endheaders()

        if request_body:
            h.send(request_body)

        errcode, errmsg, headers = h.getreply()

        if errcode != 200:
            raise xmlrpclib.ProtocolError(
                host + handler,
                errcode, errmsg,
                headers
                )

        self.verbose = verbose
        return self.parse_response(h.getfile())

class TcpkeepHTTPSConnection(m2httpslib.HTTPSConnection):

    def connect(self):
        # taken from m2httpslib.HTTPSConnection.connect(self)
        # of m2crypto-0.18. Modified a bit to support also m2crypto-0.16.
        self.sock = m2SSL.Connection(self.ssl_ctx)
        if 'session' in dir(self) and self.session:
            self.sock.set_session(self.session)

        self.sock.settimeout(CONNECTTIMEOUT)

        # after TCP_KEEPIDLE seconds of silence, TCP_KEEPCNT probes would be
        # sent, TCP_KEEPINTVL seconds apart of each other. If all of them fail,
        # the socket is closed.
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        self.sock.setsockopt(socket.SOL_TCP, socket.TCP_KEEPIDLE, KEEPIDLE)
        self.sock.setsockopt(socket.SOL_TCP, socket.TCP_KEEPINTVL, KEEPINTVL)
        self.sock.setsockopt(socket.SOL_TCP, socket.TCP_KEEPCNT, KEEPCNT)

        self.sock.connect((self.host, self.port))

class TcpkeepHTTPS(m2httpslib.HTTPS):
    _connection_class = TcpkeepHTTPSConnection

