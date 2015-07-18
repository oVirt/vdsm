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
from SimpleXMLRPCServer import SimpleXMLRPCDispatcher
from SimpleXMLRPCServer import SimpleXMLRPCRequestHandler
import SocketServer
import sys

from .config import config
from .executor import TaskQueue


class IPXMLRPCRequestHandler(SimpleXMLRPCRequestHandler):

    if config.getboolean('vars', 'xmlrpc_http11'):
        protocol_version = "HTTP/1.1"
    else:
        protocol_version = "HTTP/1.0"


class ConnectedTCPServer(SocketServer.TCPServer, object):
    """
    ConnectedTCPServer provides ability to add connected sockets
    for TCPServer to process. New connections are put to the queue
    and TCPServers gets them by calling get_request method.
    """
    _STOP = (None, None)

    def __init__(self, RequestHandlerClass):
        super(ConnectedTCPServer, self).__init__(None, RequestHandlerClass,
                                                 bind_and_activate=False)
        # TODO provide proper limit for this queue
        self.queue = TaskQueue(sys.maxint)

    def add(self, connected_socket, socket_address):
        self.queue.put((connected_socket, socket_address))

    def get_request(self):
        return self.queue.get()

    def server_close(self):
        self.queue.clear()
        self.queue.put(self._STOP)

    def verify_request(self, request, client_address):
        if not request or not client_address:
            return False
        return True


class ConnectedSimpleXmlRPCServer(ConnectedTCPServer,
                                  SimpleXMLRPCDispatcher):
    """
    Code based on Python 2.7's SimpleXMLRPCServer.SimpleXMLRPCServer.__init__
    """

    def __init__(self, requestHandler, logRequests=True, allow_none=False,
                 encoding=None):
        self.logRequests = logRequests

        SimpleXMLRPCDispatcher.__init__(self, allow_none=allow_none,
                                        encoding=encoding)
        ConnectedTCPServer.__init__(self, requestHandler)


class IPXMLRPCServer(ConnectedSimpleXmlRPCServer):

    # Create daemon threads when mixed with SocketServer.ThreadingMixIn
    daemon_threads = True

    def __init__(self, requestHandler=IPXMLRPCRequestHandler,
                 logRequests=True, allow_none=False, encoding=None,
                 bind_and_activate=False):
        ConnectedSimpleXmlRPCServer.__init__(
            self, requestHandler=requestHandler,
            logRequests=logRequests, allow_none=allow_none,
            encoding=encoding)


# Threaded version of SimpleXMLRPCServer
class SimpleThreadedXMLRPCServer(SocketServer.ThreadingMixIn,
                                 IPXMLRPCServer):
    pass
