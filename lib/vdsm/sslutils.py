#
# Copyright 2014 Red Hat, Inc.
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
import httplib
import socket
import ssl
import xmlrpclib

from M2Crypto import SSL, X509, threading


DEFAULT_ACCEPT_TIMEOUT = 5

# M2Crypto.threading needs initialization.
# See https://bugzilla.redhat.com/482420
threading.init()


class SSLSocket(object):
    def __init__(self, connection):
        self.connection = connection
        self._data = None

    def gettimeout(self):
        return self.connection.socket.gettimeout()

    def settimeout(self, *args, **kwargs):
        settimeout = getattr(self.connection, 'settimeout',
                             self.connection.socket.settimeout)
        return settimeout(*args, **kwargs)

    def close(self):
        self.connection.shutdown(socket.SHUT_RDWR)
        self.connection.close()

    def fileno(self):
        return self.connection.fileno()

    # M2C do not provide message peek so
    # we buffer first consumed message
    def read(self, size=4096, flag=None):
        result = None
        if flag == socket.MSG_PEEK:
            self._data = self.connection.read(size)
            result = self._data
        else:
            if self._data:
                result = self._data
                self._data = None
            else:
                result = self.connection.read(size)
        return result
    recv = read

    def pending(self):
        pending = self.connection.pending()
        if self._data:
            pending = pending + len(self._data)
        return pending

    def makefile(self, mode='rb', bufsize=-1):
        if mode == 'rb':
            return socket._fileobject(self, mode, bufsize)
        else:
            return self.connection.makefile(mode, bufsize)

    def __getattr__(self, name):
        return getattr(self.connection, name)


class SSLServerSocket(SSLSocket):
    def __init__(self, raw, certfile=None, keyfile=None, ca_certs=None,
                 session_id="vdsm", protocol="sslv23"):
        self.context = SSL.Context(protocol)
        self.context.set_session_id_ctx(session_id)

        if certfile and keyfile:
            self.context.load_cert_chain(certfile, keyfile)

        def verify(context, certificate, error, depth, result):
            if not result:
                certificate = X509.X509(certificate)

            return result

        if ca_certs:
            self.context.load_verify_locations(ca_certs)
            self.context.set_verify(
                mode=SSL.verify_peer | SSL.verify_fail_if_no_peer_cert,
                depth=10,
                callback=verify)

        self.connection = SSL.Connection(self.context, sock=raw)

        self.accept_timeout = DEFAULT_ACCEPT_TIMEOUT

    def fileno(self):
        return self.connection.socket.fileno()

    def accept(self):
        client, address = self.connection.socket.accept()
        client = SSL.Connection(self.context, client)
        client.addr = address
        try:
            client.setup_ssl()
            client.set_accept_state()
            client.settimeout(self.accept_timeout)
            client.accept_ssl()
            client.settimeout(None)
        except SSL.SSLError as e:
            raise SSL.SSLError("%s, client %s" % (e, address[0]))

        client = SSLSocket(client)

        return client, address


class SSLContext(object):
    def __init__(self, cert_file, key_file, ca_cert=None, session_id="SSL",
                 protocol="sslv23"):
        self.cert_file = cert_file
        self.key_file = key_file
        self.ca_cert = ca_cert
        self.session_id = session_id
        self.protocol = protocol
        self._initContext()

    def _loadCertChain(self):
        if self.cert_file and self.key_file:
            self.context.load_cert_chain(self.cert_file, self.key_file)

    def _verify(self, context, certificate, error, depth, result):
        if not result:
            certificate = X509.X509(certificate)
        return result

    def _loadCAs(self):
        context = self.context

        if self.ca_cert:
            context.load_verify_locations(self.ca_cert)
            context.set_verify(
                mode=SSL.verify_peer | SSL.verify_fail_if_no_peer_cert,
                depth=10,
                callback=self._verify)

    def _initContext(self):
        self.context = context = SSL.Context(self.protocol)
        context.set_session_id_ctx(self.session_id)

        self._loadCertChain()
        self._loadCAs()

    def wrapSocket(self, sock):
        context = self.context
        return SSLSocket(SSL.Connection(context, sock=sock), self)


class VerifyingHTTPSConnection(httplib.HTTPSConnection):
    def __init__(self, host, port=None, key_file=None, cert_file=None,
                 strict=None, timeout=socket._GLOBAL_DEFAULT_TIMEOUT,
                 ca_certs=None, cert_reqs=ssl.CERT_REQUIRED):
        httplib.HTTPSConnection.__init__(self, host, port, key_file, cert_file,
                                         strict, timeout)
        self.ca_certs = ca_certs
        self.cert_reqs = cert_reqs

    def connect(self):
        "Connect to a host on a given (SSL) port."

        sock = socket.create_connection((self.host, self.port), self.timeout)
        if self._tunnel_host:
            self.sock = sock
            self._tunnel()
        # DK added: pass ca_cert to sslsocket
        self.sock = ssl.wrap_socket(sock, self.key_file, self.cert_file,
                                    ca_certs=self.ca_certs, server_side=False,
                                    cert_reqs=self.cert_reqs)


class VerifyingSafeTransport(xmlrpclib.SafeTransport):
    def __init__(self, use_datetime=0, key_file=None, cert_file=None,
                 ca_certs=None, cert_reqs=ssl.CERT_REQUIRED):
        xmlrpclib.SafeTransport.__init__(self, use_datetime)
        self.key_file = key_file
        self.cert_file = cert_file
        self.ca_certs = ca_certs
        self.cert_reqs = cert_reqs

    def make_connection(self, host):
        """Return VerifyingHTTPS object that is aware of ca_certs, and will
        create VerifyingHTTPSConnection.
        In Python 2.7, return VerifyingHTTPSConnection object
        """
        chost, self._extra_headers, x509 = self.get_host_info(host)
        if hasattr(xmlrpclib.SafeTransport, "single_request"):   # Python 2.7
            return VerifyingHTTPSConnection(
                chost, None, key_file=self.key_file, strict=None,
                cert_file=self.cert_file, ca_certs=self.ca_certs,
                cert_reqs=self.cert_reqs)
        else:
            return VerifyingHTTPS(
                chost, None, key_file=self.key_file,
                cert_file=self.cert_file, ca_certs=self.ca_certs,
                cert_reqs=self.cert_reqs)


class VerifyingHTTPS(httplib.HTTPS):
    _connection_class = VerifyingHTTPSConnection

    def __init__(self, host='', port=None, key_file=None, cert_file=None,
                 strict=None, ca_certs=None, cert_reqs=ssl.CERT_REQUIRED):
        """A ca_cert-aware HTTPS object,
        that creates a VerifyingHTTPSConnection
        """
        # provide a default host, pass the X509 cert info

        # urf. compensate for bad input.
        if port == 0:
            port = None
        self._setup(self._connection_class(host, port, key_file,
                                           cert_file, strict,
                                           ca_certs=ca_certs,
                                           cert_reqs=cert_reqs))

        # we never actually use these for anything, but we keep them
        # here for compatibility with post-1.5.2 CVS.
        self.key_file = key_file
        self.cert_file = cert_file
