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

from Queue import Queue
from SimpleXMLRPCServer import SimpleXMLRPCDispatcher
from SimpleXMLRPCServer import SimpleXMLRPCRequestHandler
import SocketServer
import sys
import traceback

from .config import config


class IPXMLRPCRequestHandler(SimpleXMLRPCRequestHandler):

    if config.getboolean('vars', 'xmlrpc_http11'):
        protocol_version = "HTTP/1.1"
    else:
        protocol_version = "HTTP/1.0"

    # Override Python 2.6 version to support HTTP 1.1.
    #
    # This is the same code as Python 2.6, not shutting down the connection
    # when a request is finished. The server is responsible for closing the
    # connection, based on the http version and keep-alive and connection:
    # close headers.
    #
    # Additionally, add "Content-Length: 0" header on internal errors, when we
    # don't send any content. This is required by HTTP 1.1, otherwise the
    # client does not have any clue that the response was finished.
    #
    # These changes were taken from Python 2.7 version of this class. If we are
    # running on Python 2.7, these changes are not needed, hence we override
    # the methods only on Python 2.6.

    if sys.version_info[:2] == (2, 6):

        def do_POST(self):
            # Check that the path is legal
            if not self.is_rpc_path_valid():
                self.report_404()
                return

            try:
                # Get arguments by reading body of request.
                # We read this in chunks to avoid straining
                # socket.read(); around the 10 or 15Mb mark, some platforms
                # begin to have problems (bug #792570).
                max_chunk_size = 10 * 1024 * 1024
                size_remaining = int(self.headers["content-length"])
                L = []
                while size_remaining:
                    chunk_size = min(size_remaining, max_chunk_size)
                    chunk = self.rfile.read(chunk_size)
                    if not chunk:
                        break
                    L.append(chunk)
                    size_remaining -= len(L[-1])
                data = ''.join(L)

                # In previous versions of SimpleXMLRPCServer, _dispatch
                # could be overridden in this class, instead of in
                # SimpleXMLRPCDispatcher. To maintain backwards compatibility,
                # check to see if a subclass implements _dispatch and dispatch
                # using that method if present.
                response = self.server._marshaled_dispatch(
                    data, getattr(self, '_dispatch', None))
            except Exception, e:
                # This should only happen if the module is buggy
                # internal error, report as HTTP server error
                self.send_response(500)

                # Send information about the exception if requested
                if getattr(self.server, '_send_traceback_header', False):
                    self.send_header("X-exception", str(e))
                    self.send_header("X-traceback", traceback.format_exc())

                self.send_header("Content-length", '0')
                self.end_headers()
            else:
                # got a valid XML RPC response
                self.send_response(200)
                self.send_header("Content-type", "text/xml")
                self.send_header("Content-length", str(len(response)))
                self.end_headers()
                self.wfile.write(response)

        def report_404(self):
            self.send_response(404)
            response = 'No such page'
            self.send_header("Content-type", "text/plain")
            self.send_header("Content-length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)


class ConnectedTCPServer(SocketServer.TCPServer, object):

    def __init__(self, RequestHandlerClass):
        super(ConnectedTCPServer, self).__init__(None, RequestHandlerClass,
                                                 bind_and_activate=False)
        self.queue = Queue()

    def add(self, connected_socket, socket_address):
        self.queue.put((connected_socket, socket_address))

    def get_request(self):
        return self.queue.get(True)

    def server_close(self):
        self.queue.put((None, None))

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
