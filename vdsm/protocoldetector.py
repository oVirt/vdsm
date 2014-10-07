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

import errno
import logging
import os
import select
import socket
import time

from M2Crypto import SSL

from vdsm.utils import traceback
from vdsm import utils


def _is_handshaking(sock):
    if not hasattr(sock, "is_handshaking"):
        return False

    return sock.is_handshaking


class MultiProtocolAcceptor:
    """
    Provides multiple protocol support on a single port.

    MultiProtocolAcceptor binds and listen on a single port. It accepts
    incoming connections and handles handshake if required. Next it peeks
    into the first bytes sent to detect the protocol, and pass the connection
    to the server handling this protocol.

    To support a new protocol, register a detector object using
    add_detector. Protocol detectors must implement this interface:

    class ProtocolDetector(object):
        NAME = "protocol name"

        # How many bytes are needed to detect this protocol
        REQUIRED_SIZE = 6

        def detect(self, data):
            Given first bytes read from the connection, try to detect the
            protocol. Returns True if protocol is detected.

        def handleSocket(self, client_socket, socket_address):
            Called after detect() succeeded. The detector owns the socket and
            is responsible for closing it.
    """
    log = logging.getLogger("vds.MultiProtocolAcceptor")

    CLEANUP_INTERVAL = 30.0

    def __init__(self, host, port, sslctx=None):
        self._sslctx = sslctx
        self._host = host
        self._port = port

        self._read_fd, self._write_fd = os.pipe()
        utils.set_non_blocking(self._read_fd)
        utils.closeOnExec(self._read_fd)
        utils.set_non_blocking(self._write_fd)
        utils.closeOnExec(self._write_fd)

        self._socket = self._create_socket(host, port)
        self._poller = select.poll()
        self._poller.register(self._socket, select.POLLIN)
        self._poller.register(self._read_fd, select.POLLIN)
        self._pending_connections = {}
        self._handlers = []
        self._next_cleanup = 0
        self._required_size = None

    @traceback(on=log.name)
    def serve_forever(self):
        self.log.debug("Acceptor running")
        self._required_size = max(h.REQUIRED_SIZE for h in self._handlers)
        self.log.debug("Using required_size=%d", self._required_size)
        self._next_cleanup = time.time() + self.CLEANUP_INTERVAL
        try:
            while True:
                try:
                    self._process_events()
                except _Stopped:
                    break
                except Exception:
                    self.log.exception("Unhandled exception")
        finally:
            self._cleanup()

    def _process_events(self):
        timeout = max(self._next_cleanup - time.time(), 0)
        events = self._poller.poll(timeout)

        for fd, event in events:
            if event & select.POLLIN:
                if fd is self._read_fd:
                    self._maybe_stop()
                elif fd is self._socket.fileno():
                    self._accept_connection()
                else:
                    self._handle_connection_read(fd)
            if event & select.POLLOUT:
                    self._handle_connection_write(fd)

        now = time.time()
        if now > self._next_cleanup:
            self._next_cleanup = now + self.CLEANUP_INTERVAL
            self._cleanup_pending_connections()

    def _cleanup(self):
        self.log.debug("Cleaning Acceptor")

        for _, (_, client_socket) in self._pending_connections.items():
            self._remove_connection(client_socket)
            client_socket.close()

        self._poller.unregister(self._socket)
        self._poller.unregister(self._read_fd)
        self._socket.close()
        os.close(self._read_fd)
        os.close(self._write_fd)

    def _cleanup_pending_connections(self):
        for _, (accepted, client_socket) in self._pending_connections.items():
            if time.time() - accepted > self.CLEANUP_INTERVAL:
                self._remove_connection(client_socket)
                client_socket.close()

    def detect_protocol(self, data):
        for handler in self._handlers:
            if handler.detect(data):
                return handler
        raise _CannotDetectProtocol()

    def add_detector(self, detector):
        self.log.debug("adding detector: %s", detector)
        self._handlers.append(detector)

    def stop(self):
        self.log.debug("Stopping Acceptor")
        while True:
            try:
                os.write(self._write_fd, "1")
            except OSError as e:
                if e.errno in (errno.EPIPE, errno.EBADF):
                    # Detector already stopped
                    return
                if e.errno != errno.EINTR:
                    raise
            else:
                break

    def _maybe_stop(self):
        try:
            if os.read(self._read_fd, 1) == '1':
                raise _Stopped()
        except OSError as e:
            if e.errno not in (errno.EAGAIN, errno.EWOULDBLOCK):
                raise

    def _accept_connection(self):
        client_socket, address = self._socket.accept()
        if self._sslctx:
            client_socket = self._sslctx.wrapSocket(client_socket)
            # Older versions of M2Crypto ignore nbio and retry internally
            # if timeout is set.
            client_socket.settimeout(None)
            client_socket.address = address
            try:
                client_socket.setup_ssl()
                client_socket.set_accept_state()
            except SSL.SSLError as e:
                self.log.warning("Error setting up ssl: %s", e)
                client_socket.close()
                return

            client_socket.is_handshaking = True

        self._add_connection(client_socket)

    def _add_connection(self, socket):
        host, port = socket.getpeername()
        self.log.debug("Adding connection from %s:%d", host, port)
        socket.setblocking(0)
        self._pending_connections[socket.fileno()] = (time.time(),
                                                      socket)
        if _is_handshaking(socket):
            self._poller.register(socket, select.POLLIN | select.POLLOUT)
        else:
            self._poller.register(socket, select.POLLIN)

    def _remove_connection(self, socket):
        self._poller.unregister(socket)
        del self._pending_connections[socket.fileno()]
        socket.setblocking(1)
        host, port = socket.getpeername()
        self.log.debug("Connection removed from %s:%d", host, port)

    def _process_handshake(self, socket):
        try:
            socket.is_handshaking = (socket.accept_ssl() == 0)
        except Exception as e:
            self.log.debug("Error during handshake: %s", e)
            socket.close()
        else:
            if not socket.is_handshaking:
                self._poller.modify(socket, select.POLLIN)

    def _handle_connection_write(self, fd):
        _, client_socket = self._pending_connections[fd]
        if _is_handshaking(client_socket):
            self._process_handshake(client_socket)

    def _handle_connection_read(self, fd):
        _, client_socket = self._pending_connections[fd]
        if _is_handshaking(client_socket):
            self._process_handshake(client_socket)
            return

        try:
            data = client_socket.recv(self._required_size, socket.MSG_PEEK)
        except socket.error as e:
            if e.errno not in (errno.EAGAIN, errno.EWOULDBLOCK):
                self.log.warning("Unable to read data: %s", e)
                self._remove_connection(client_socket)
                client_socket.close()
            return

        if data is None:
            # None is returned when ssl socket needs to read more data
            return

        self._remove_connection(client_socket)
        try:
            handler = self.detect_protocol(data)
        except _CannotDetectProtocol:
            self.log.warning("Unrecognized protocol: %r", data)
            client_socket.close()
        else:
            host, port = client_socket.getpeername()
            self.log.debug("Detected protocol %s from %s:%d",
                           handler.NAME, host, port)
            handler.handleSocket(client_socket, (host, port))

    def _create_socket(self, host, port):
        addr = socket.getaddrinfo(host, port, socket.AF_INET,
                                  socket.SOCK_STREAM)
        if not addr:
            raise Exception("Could not translate address '%s:%s'"
                            % (self._host, str(self._port)))
        server_socket = socket.socket(addr[0][0], addr[0][1], addr[0][2])
        utils.closeOnExec(server_socket.fileno())
        server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server_socket.bind(addr[0][4])
        server_socket.listen(5)

        server_socket.setblocking(0)
        return server_socket


class _CannotDetectProtocol(Exception):
    pass


class _Stopped(Exception):
    pass
