#
# Copyright 2015-2017 Red Hat, Inc.
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
import socket
import ssl
from netaddr import IPAddress
from netaddr.core import AddrFormatError

from ssl import SSLError
from vdsm import constants
from vdsm.common.time import monotonic_time
from .config import config


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
            # pylint: disable=no-member
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
    # pylint: disable=no-member
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
            if config.getboolean('vars', 'verify_client_cert'):
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
                    return self.compare_names(addr, value)

        return False

    @staticmethod
    def compare_names(src_addr, cert_common_name):
        src_addr = SSLHandshakeDispatcher._normalize_ip_address(src_addr)
        try:
            cert_common_name = \
                SSLHandshakeDispatcher._normalize_ip_address(
                    cert_common_name)
        except AddrFormatError:
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
        ip = IPAddress(addr)
        if ip.is_ipv4_mapped():
            addr = str(ip.ipv4())

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
                dispatcher.close()
                raise
        else:
            self.want_read = self.want_write = True
            self._is_handshaking = False

    def handle_close(self, dispatcher):
        dispatcher.close()


def create_ssl_context():
        sslctx = None
        if config.getboolean('vars', 'ssl'):
            # pylint: disable=no-member
            protocol = (
                ssl.PROTOCOL_SSLv23
                if config.get('vars', 'ssl_protocol') == 'sslv23'
                else ssl.PROTOCOL_TLSv1_2
            )
            sslctx = SSLContext(key_file=constants.KEY_FILE,
                                cert_file=constants.CERT_FILE,
                                ca_certs=constants.CA_FILE, protocol=protocol)
        return sslctx
