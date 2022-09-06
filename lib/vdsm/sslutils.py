# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import

import ipaddress
import logging
import socket
import ssl

import six

from ssl import SSLError
from vdsm.common import pki
from vdsm.common.time import monotonic_time
from .config import config


class SSLSocket(object):
    def __init__(self, sock):
        self.sock = sock
        self._data = b''

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
                    self._data = b''
                else:
                    result = self.sock.read(size)
        except SSLError as e:
            # pylint: disable=no-member
            if e.errno != ssl.SSL_ERROR_WANT_READ:
                raise

        return result
    recv = read

    def recv_into(self, memview, nbytes=None, flags=0):
        if nbytes == 0 or nbytes is None:
            readlen = len(memview)
        elif nbytes < 0:
            raise ValueError("negative buffersize in recvfrom_into")
        elif nbytes > len(memview):
            raise ValueError("nbytes is greater than the length of the buffer")
        else:
            readlen = nbytes
        data = self.recv(readlen, flags)
        datalen = len(data)
        memview[:datalen] = data
        return datalen

    def pending(self):
        pending = self.sock.pending()
        if self._data:
            pending = pending + len(self._data)
        return pending

    def __getattr__(self, name):
        return getattr(self.sock, name)

    def makefile(self, mode='rb', bufsize=-1):
        if mode == 'rb':
            if six.PY2:
                # pylint: disable=no-member
                return socket._fileobject(self, mode, bufsize)
            else:
                return socket.socket.makefile(self, mode, bufsize)
        else:
            return self.sock.makefile(mode, bufsize)


class SSLContext(object):

    def __init__(self, cert_file, key_file, ca_certs=None):
        self.cert_file = cert_file
        self.key_file = key_file
        self.ca_certs = ca_certs

    def wrapSocket(self, sock):
        return SSLSocket(
            ssl.wrap_socket(sock,
                            keyfile=self.key_file,
                            certfile=self.cert_file,
                            server_side=False,
                            cert_reqs=ssl.CERT_REQUIRED,
                            ca_certs=self.ca_certs)
        )


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
    LOCAL_ADDRESSES = ('127.0.0.1', '::1')

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

        # pylint: disable=no-member
        protocol = ssl.PROTOCOL_TLSv1_2 if six.PY2 else ssl.PROTOCOL_TLS
        # TODO: Drop 'protocol' param when purging py2
        context = ssl.SSLContext(protocol)
        context.load_verify_locations(self._sslctx.ca_certs, None, None)
        context.verify_mode = ssl.CERT_REQUIRED
        context.load_cert_chain(self._sslctx.cert_file, self._sslctx.key_file)

        client_socket = SSLSocket(
            context.wrap_socket(client_socket,
                                server_side=True,
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
            if config.getboolean('vars', 'verify_client_cert'):
                peercert = dispatcher.socket.getpeercert()
                peername = dispatcher.socket.getpeername()[0]
                if not self._verify_host(peercert, peername):
                    self.log.error(
                        "peer certificate '%s' does not match host name '%s'",
                        peercert, peername)
                    dispatcher.socket.close()
                    return

            self._handshake_finished_handler(dispatcher)

    def _verify_host(self, peercert, addr):
        if not peercert:
            return False

        for sub in peercert.get("subject", ()):
            for key, value in sub:
                if key == "commonName":
                    return self.compare_names(addr, value)

        return False

    @staticmethod
    def compare_names(src_addr, cert_common_name):
        src_addr = SSLHandshakeDispatcher._normalize_ip_address(src_addr)
        try:
            cert_common_name = \
                SSLHandshakeDispatcher._normalize_ip_address(
                    cert_common_name)
        except ValueError:
            # used name not address
            pass

        if src_addr == cert_common_name:
            return True
        elif src_addr in SSLHandshakeDispatcher.LOCAL_ADDRESSES:
            return True
        else:
            name, aliaslist, addresslist = socket.gethostbyaddr(src_addr)
            hostnames = [name] + aliaslist + addresslist

            return any(cert_common_name.lower() == hostname.lower()
                       for hostname in hostnames)

    @staticmethod
    def _normalize_ip_address(addr):
        """
        When we used mapped ipv4 (starting with ::FFFF/96) we need to
        normalize it to ipv4 in order to compare it with value used
        in commonName in the certificate.
        """
        ip = ipaddress.ip_address(addr)
        if ip.version == 6 and ip.ipv4_mapped:
            addr = str(ip.ipv4_mapped)

        return addr

    # pylint: disable=no-member
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
                self.log.error("ssl handshake: SSLError, address: {}".format(
                    dispatcher.socket.getpeername()[0]))
                dispatcher.close()
        except socket.error:
            self.log.error("ssl handshake: socket error, address: {}".format(
                dispatcher.socket.getpeername()[0]))
            dispatcher.close()
        else:
            self.want_read = self.want_write = True
            self._is_handshaking = False

    def handle_close(self, dispatcher):
        dispatcher.close()


def create_ssl_context():
    sslctx = None
    if config.getboolean('vars', 'ssl'):
        sslctx = SSLContext(key_file=pki.KEY_FILE,
                            cert_file=pki.CERT_FILE,
                            ca_certs=pki.CA_FILE)
    return sslctx
