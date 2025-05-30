# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

import os
import socket
import socketserver
import xmlrpc.server


class UnixXmlRpcHandler(xmlrpc.server.SimpleXMLRPCRequestHandler):
    disable_nagle_algorithm = False


# This class implements a XML-RPC server that binds to a UNIX socket. The path
# to the UNIX socket to create methods must be provided.
class UnixXmlRpcServer(socketserver.UnixStreamServer,
                       xmlrpc.server.SimpleXMLRPCDispatcher):
    address_family = socket.AF_UNIX
    allow_address_reuse = True

    def __init__(self, sock_path, request_handler=UnixXmlRpcHandler,
                 logRequests=0):
        if os.path.exists(sock_path):
            os.unlink(sock_path)
        self.logRequests = logRequests
        xmlrpc.server.SimpleXMLRPCDispatcher.__init__(self,
                                                      encoding=None,
                                                      allow_none=1)
        socketserver.UnixStreamServer.__init__(self, sock_path,
                                               request_handler)
