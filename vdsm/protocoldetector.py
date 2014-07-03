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
import fcntl
import logging
import os
import select
import socket
import time

from vdsm.sslutils import SSLServerSocket
from vdsm.utils import traceback
from vdsm import utils


class MultiProtocolAcceptor:
    log = logging.getLogger("vds.MultiProtocolAcceptor")

    READ_ONLY_MASK = (select.POLLIN | select.POLLPRI | select.POLLHUP
                      | select.POLLERR)
    CLEANUP_INTERVAL = 30.0

    def __init__(self, host, port, sslctx=None):
        self._sslctx = sslctx
        self._host = host
        self._port = port

        self._read_fd, self._write_fd = os.pipe()
        self._set_non_blocking(self._read_fd)
        utils.closeOnExec(self._read_fd)
        self._set_non_blocking(self._write_fd)
        utils.closeOnExec(self._write_fd)

        self._socket = self._create_socket(host, port)
        self._poller = select.poll()
        self._poller.register(self._socket, self.READ_ONLY_MASK)
        self._poller.register(self._read_fd, self.READ_ONLY_MASK)
        self._pending_connections = {}
        self._handlers = []
        self._next_cleanup = 0
        self._required_size = None

    def _set_non_blocking(self, fd):
        flags = fcntl.fcntl(fd, fcntl.F_GETFL, 0)
        flags = flags | os.O_NONBLOCK
        fcntl.fcntl(fd, fcntl.F_SETFL, flags)

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
            if event & (select.POLLIN | select.POLLPRI):
                if fd is self._read_fd:
                    self._maybe_stop()
                elif fd is self._socket.fileno():
                    self._accept_connection()
                else:
                    self._handle_connection_read(fd)
            else:
                pass

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
        client_socket, _ = self._socket.accept()
        self._add_connection(client_socket)

    def _add_connection(self, socket):
        host, port = socket.getpeername()
        self.log.debug("Adding connection from %s:%d", host, port)
        socket.setblocking(0)
        self._pending_connections[socket.fileno()] = (time.time(),
                                                      socket)
        self._poller.register(socket, self.READ_ONLY_MASK)

    def _remove_connection(self, socket):
        self._poller.unregister(socket)
        del self._pending_connections[socket.fileno()]
        socket.setblocking(1)
        host, port = socket.getpeername()
        self.log.debug("Connection removed from %s:%d", host, port)

    def _handle_connection_read(self, fd):
        _, client_socket = self._pending_connections[fd]
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

        if self._sslctx:
            server_socket = SSLServerSocket(raw=server_socket,
                                            certfile=self._sslctx.cert_file,
                                            keyfile=self._sslctx.key_file,
                                            ca_certs=self._sslctx.ca_cert)

        server_socket.setblocking(0)
        return server_socket


class _CannotDetectProtocol(Exception):
    pass


class _Stopped(Exception):
    pass
