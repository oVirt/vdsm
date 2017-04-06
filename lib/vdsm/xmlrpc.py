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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#

from __future__ import absolute_import

import logging
from six.moves.xmlrpc_server import SimpleXMLRPCDispatcher
from six.moves.xmlrpc_server import SimpleXMLRPCRequestHandler
import socket
import sys

from . import concurrent
from .executor import TaskQueue


class IPXMLRPCRequestHandler(SimpleXMLRPCRequestHandler):

    protocol_version = "HTTP/1.1"


class SimpleThreadedXMLRPCServer(SimpleXMLRPCDispatcher):
    """
    This server does not listen to to connections; the user is responsible for
    accepting connections and adding them to the server.

    For each connection added, request_handler is invoked in a new thread,
    handling all requests sent over this connection.
    """

    _STOP = object()

    log = logging.getLogger("vds.XMLRPCServer")

    def __init__(self, requestHandler=IPXMLRPCRequestHandler,
                 logRequests=False, allow_none=False, encoding=None):
        SimpleXMLRPCDispatcher.__init__(self, allow_none=allow_none,
                                        encoding=encoding)

        self.requestHandler = requestHandler
        self.logRequests = logRequests

        # TODO provide proper limit for this queue
        self.queue = TaskQueue(sys.maxint)

    def add(self, connected_socket, socket_address):
        self.queue.put((connected_socket, socket_address))

    def handle_request(self):
        sock, addr = self.queue.get()
        if sock is self._STOP:
            return
        self.log.info("Starting request handler for %s:%d", addr[0], addr[1])
        t = concurrent.thread(self._process_requests, args=(sock, addr),
                              log=self.log)
        t.start()

    def server_close(self):
        self.queue.clear()
        self.queue.put((self._STOP, self._STOP))

    def _process_requests(self, sock, addr):
        self.log.info("Request handler for %s:%d started", addr[0], addr[1])
        try:
            self.requestHandler(sock, addr, self)
        except Exception:
            self.log.exception("Unhandled exception in request handler for "
                               "%s:%d", addr[0], addr[1])
        finally:
            self._shutdown_connection(sock)
        self.log.info("Request handler for %s:%d stopped", addr[0], addr[1])

    def _shutdown_connection(self, sock):
        try:
            sock.shutdown(socket.SHUT_WR)
        except socket.error:
            pass  # Some platforms may raise ENOTCONN here
        finally:
            sock.close()
