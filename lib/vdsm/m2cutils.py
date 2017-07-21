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
from __future__ import absolute_import
import logging
from six.moves import http_client as httplib
from six.moves import xmlrpc_client as xmlrpclib
import socket
import ssl

from vdsm import compat
from vdsm import constants
from vdsm.utils import (
    monotonic_time,
)
from .config import config

try:
    from M2Crypto import m2, SSL, X509, threading
    from M2Crypto.SSL import SSLError
except ImportError as e:
    raise compat.Unsupported(str(e))

CLIENT_PROTOCOL = "sslv23"
SSL_OP_NO_TLSv1_1 = 268435456

DEFAULT_ACCEPT_TIMEOUT = 5
SOCKET_DEFAULT_TIMEOUT = socket._GLOBAL_DEFAULT_TIMEOUT

# M2Crypto.threading needs initialization.
# See https://bugzilla.redhat.com/482420
threading.init()


class SSLSocket(object):
    def __init__(self, connection):
        self.connection = connection
        self._data = ''

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
            bytes_left = size - len(self._data)
            if bytes_left > 0:
                self._data += self.connection.read(bytes_left)
            result = self._data
        else:
            if self._data:
                result = self._data
                self._data = ''
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
        if config.getboolean('devel', 'm2c_debug_enable'):
            self.context.set_info_callback()
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
        except SSLError as e:
            raise SSLError("%s, client %s" % (e, address[0]))

        client = SSLSocket(client)

        return client, address


class SSLContext(object):
    def __init__(self, cert_file, key_file, ca_certs=None, session_id="SSL",
                 excludes=0, protocol=CLIENT_PROTOCOL):
        self.cert_file = cert_file
        self.key_file = key_file
        self.ca_certs = ca_certs
        self.session_id = session_id
        self.excludes = excludes
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

        if self.ca_certs:
            context.load_verify_locations(self.ca_certs)
            context.set_verify(
                mode=SSL.verify_peer | SSL.verify_fail_if_no_peer_cert,
                depth=10,
                callback=self._verify)

    def _initContext(self):
        self.context = context = SSL.Context(self.protocol)
        if config.getboolean('devel', 'm2c_debug_enable'):
            self.context.set_info_callback()
        context.set_session_id_ctx(self.session_id)

        if self.excludes != 0:
            context.set_options(
                m2.SSL_OP_NO_SSLv2 | m2.SSL_OP_NO_SSLv3 | self.excludes)

        self._loadCertChain()
        self._loadCAs()

    def wrapSocket(self, sock):
        context = self.context
        return SSLSocket(SSL.Connection(context, sock=sock))


class VerifyingHTTPSConnection(httplib.HTTPSConnection):
    def __init__(self, host, port=None, key_file=None, cert_file=None,
                 strict=None, timeout=SOCKET_DEFAULT_TIMEOUT,
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
                 ca_certs=None, cert_reqs=ssl.CERT_REQUIRED,
                 timeout=SOCKET_DEFAULT_TIMEOUT):
        xmlrpclib.SafeTransport.__init__(self, use_datetime)
        self.key_file = key_file
        self.cert_file = cert_file
        self.ca_certs = ca_certs
        self.cert_reqs = cert_reqs
        self._timeout = timeout

    def make_connection(self, host):
        chost, self._extra_headers, x509 = self.get_host_info(host)
        return VerifyingHTTPSConnection(
            chost, None, key_file=self.key_file, strict=None,
            cert_file=self.cert_file, timeout=self._timeout,
            ca_certs=self.ca_certs,
            cert_reqs=self.cert_reqs)


class SSLHandshakeDispatcher(object):
    """
    SSLHandshakeDispatcher is dispatcher implementation to process ssl
    handshake in asynchronous way. Once we are done with handshaking we
    we need to swap our dispatcher implementation with message processing
    dispatcher. We use handshake_finished_handler function to perform
    swapping. The handler implementation need to invoke

    dispatcher.switch_implementation()

    where we provide message processing dispatcher as parameter.
    """
    log = logging.getLogger("ProtocolDetector.SSLHandshakeDispatcher")
    SSL_HANDSHAKE_TIMEOUT = 10

    def __init__(
        self,
        sslctx,
        handshake_finished_handler,
        handshake_timeout=SSL_HANDSHAKE_TIMEOUT,
    ):
        self._give_up_at = monotonic_time() + handshake_timeout
        self._has_been_set_up = False
        self._is_handshaking = True
        self._sslctx = sslctx
        self._handshake_finished_handler = handshake_finished_handler

    def _set_up_socket(self, dispatcher):
        client_socket = dispatcher.socket
        client_socket = self._sslctx.wrapSocket(client_socket)
        # Older versions of M2Crypto ignore nbio and retry internally
        # if timeout is set.
        client_socket.settimeout(None)
        client_socket.address = client_socket.getpeername()
        try:
            client_socket.setup_ssl()
            client_socket.set_accept_state()
        except SSLError as e:
            self.log.error("Error setting up ssl: %s", e)
            dispatcher.close()
            return

        dispatcher.socket = client_socket

    def next_check_interval(self):
        return max(self._give_up_at - monotonic_time(), 0)

    def readable(self, dispatcher):
        if self.has_expired():
            dispatcher.close()
            return False

        return True

    def writable(self, dispatcher):
        return False

    def has_expired(self):
        return monotonic_time() >= self._give_up_at

    def handle_read(self, dispatcher):
        if not self._has_been_set_up:
            self._set_up_socket(dispatcher)

        if self._is_handshaking:
            try:
                self._is_handshaking = (dispatcher.socket.accept_ssl() == 0)
            except Exception as e:
                self.log.error("Error during handshake: %s", e)
                dispatcher.close()

        if not self._is_handshaking:
            self._handshake_finished_handler(dispatcher)


def create_ssl_context():
    if config.getboolean('vars', 'ssl'):
        protocol = config.get('vars', 'ssl_protocol')
        excludes = protocol_name_to_int()
        sslctx = SSLContext(constants.CERT_FILE, constants.KEY_FILE,
                            ca_certs=constants.CA_FILE, protocol=protocol,
                            excludes=excludes)
        return sslctx
    else:
        return None


def protocol_name_to_int():
    excludes = 0

    for no_protocol in config.get('vars', 'ssl_excludes').split(','):
        if no_protocol != '':
            protocol = 'SSL_' + no_protocol.strip()
            if protocol == 'SSL_OP_NO_TLSv1_1':
                # missing from m2crypto
                excludes |= SSL_OP_NO_TLSv1_1
            else:
                excludes |= getattr(m2, protocol)

    return excludes
