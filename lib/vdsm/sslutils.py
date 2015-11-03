#
# Copyright 2015 Red Hat, Inc.
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
import httplib
import logging
import os
import socket
import ssl
import xmlrpclib

from ssl import SSLError
from vdsm.utils import monotonic_time
from .config import config


DEFAULT_ACCEPT_TIMEOUT = 5
SOCKET_DEFAULT_TIMEOUT = socket._GLOBAL_DEFAULT_TIMEOUT


class SSLSocket(object):
    def __init__(self, sock):
        self.sock = sock
        self._data = ''

    # ssl do not accept flag other than 0
    def read(self, size=4096, flag=None):
        result = None
        try:
            if flag == socket.MSG_PEEK:
                bytes_left = size - len(self._data)
                if bytes_left > 0:
                    self._data += self.sock.read(bytes_left)
                result = self._data
            else:
                if self._data:
                    result = self._data
                    self._data = ''
                else:
                    result = self.sock.read(size)
        except SSLError as e:
            if e.errno != ssl.SSL_ERROR_WANT_READ:
                raise

        return result
    recv = read

    def pending(self):
        pending = self.sock.pending()
        if self._data:
            pending = pending + len(self._data)
        return pending

    def __getattr__(self, name):
        return getattr(self.sock, name)

    def makefile(self, mode='rb', bufsize=-1):
        if mode == 'rb':
            return socket._fileobject(self, mode, bufsize)
        else:
            return self.sock.makefile(mode, bufsize)


class SSLContext(object):
    def __init__(self, cert_file, key_file, ca_certs=None,
                 protocol=ssl.PROTOCOL_TLSv1):
        self.cert_file = cert_file
        self.key_file = key_file
        self.ca_certs = ca_certs
        self.protocol = protocol

    def wrapSocket(self, sock):
        return SSLSocket(
            ssl.wrap_socket(sock,
                            keyfile=self.key_file,
                            certfile=self.cert_file,
                            server_side=False,
                            cert_reqs=ssl.CERT_REQUIRED,
                            ssl_version=self.protocol,
                            ca_certs=self.ca_certs)
        )


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
        self.want_read = True
        self.want_write = True
        self._sslctx = sslctx
        self._handshake_finished_handler = handshake_finished_handler

    def _set_up_socket(self, dispatcher):
        client_socket = dispatcher.socket
        client_socket = SSLSocket(
            ssl.wrap_socket(client_socket,
                            keyfile=self._sslctx.key_file,
                            certfile=self._sslctx.cert_file,
                            server_side=True,
                            cert_reqs=ssl.CERT_REQUIRED,
                            ssl_version=self._sslctx.protocol,
                            ca_certs=self._sslctx.ca_certs,
                            do_handshake_on_connect=False))

        dispatcher.socket = client_socket
        self._has_been_set_up = True

    def next_check_interval(self):
        return max(self._give_up_at - monotonic_time(), 0)

    def readable(self, dispatcher):
        if self.has_expired():
            dispatcher.close()
            return False

        return self.want_read

    def writable(self, dispatcher):
        if self.has_expired():
            dispatcher.close()
            return False

        return self.want_write

    def has_expired(self):
        return monotonic_time() >= self._give_up_at

    def handle_read(self, dispatcher):
        self._handle_io(dispatcher)

    def handle_write(self, dispatcher):
        self._handle_io(dispatcher)

    def _handle_io(self, dispatcher):
        if not self._has_been_set_up:
            self._set_up_socket(dispatcher)

        if self._is_handshaking:
            self._handshake(dispatcher)
        else:
            if not self._verify_host(dispatcher.socket.getpeercert(),
                                     dispatcher.socket.getpeername()[0]):
                self.log.error("peer certificate does not match host name")
                dispatcher.socket.close()
                return

            self._handshake_finished_handler(dispatcher)

    def _verify_host(self, peercert, addr):
        if not peercert:
            return False

        for sub in peercert.get("subject", ()):
            for key, value in sub:
                if key == "commonName":
                    return self._compare_names(addr, value)

        return False

    def _compare_names(self, addr, name):
        if addr == name or addr == '127.0.0.1':
            return True
        else:
            return name.lower() == socket.gethostbyaddr(addr)[0].lower()

    def _handshake(self, dispatcher):
        try:
            dispatcher.socket.do_handshake()
        except SSLError as err:
            self.want_read = self.want_write = False
            if err.args[0] == ssl.SSL_ERROR_WANT_READ:
                self.want_read = True
            elif err.args[0] == ssl.SSL_ERROR_WANT_WRITE:
                self.want_write = True
            else:
                dispatcher.close()
                raise
        else:
            self.want_read = self.want_write = True
            self._is_handshaking = False


def create_ssl_context():
        sslctx = None
        if config.getboolean('vars', 'ssl'):
            truststore_path = config.get('vars', 'trust_store_path')
            protocol = (
                ssl.PROTOCOL_SSLv23
                if config.get('vars', 'ssl_protocol') == 'sslv23'
                else ssl.PROTOCOL_TLSv1
            )
            sslctx = SSLContext(
                key_file=os.path.join(truststore_path, 'keys', 'vdsmkey.pem'),
                cert_file=os.path.join(truststore_path, 'certs',
                                       'vdsmcert.pem'),
                ca_certs=os.path.join(truststore_path, 'certs', 'cacert.pem'),
                protocol=protocol
            )
        return sslctx
