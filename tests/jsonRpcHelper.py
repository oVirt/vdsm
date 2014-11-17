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
import httplib
import logging
import socket
import threading

from xmlrpclib import Transport, dumps, Fault
from contextlib import contextmanager
from functools import partial
from itertools import product
from M2Crypto import SSL
from rpc.BindingXMLRPC import BindingXMLRPC, XmlDetector
from yajsonrpc.stompReactor import StompDetector
from protocoldetector import MultiProtocolAcceptor
from yajsonrpc import JsonRpcClientPool
from rpc.BindingJsonRpc import BindingJsonRpc
from sslhelper import DEAFAULT_SSL_CONTEXT

PERMUTATIONS = tuple(product((True, False), ("xml", "stomp")))

TIMEOUT = 3


class FakeClientIf(object):
    log = logging.getLogger("FakeClientIf")

    def __init__(self):
        self.threadLocal = threading.local()
        self.irs = True
        self.gluster = None

        # API module is redefined for apiTests so we need to add BLANK_UUIDs
        import API
        API.Image.BLANK_UUID = '00000000-0000-0000-0000-000000000000'
        API.StorageDomain.BLANK_UUID = '00000000-0000-0000-0000-000000000000'
        API.Volume.BLANK_UUID = "00000000-0000-0000-0000-000000000000"

    @property
    def ready(self):
        return True


@contextmanager
def constructAcceptor(log, ssl, jsonBridge):
    sslctx = DEAFAULT_SSL_CONTEXT if ssl else None
    acceptor = MultiProtocolAcceptor("127.0.0.1", 0, sslctx)
    cif = FakeClientIf()

    xml_binding = BindingXMLRPC(cif, cif.log)
    xml_binding.start()
    xmlDetector = XmlDetector(xml_binding)
    acceptor.add_detector(xmlDetector)

    json_binding = BindingJsonRpc(jsonBridge)
    json_binding.start()
    stompDetector = StompDetector(json_binding)
    acceptor.add_detector(stompDetector)

    thread = threading.Thread(target=acceptor.serve_forever,
                              name='Detector thread')
    thread.setDaemon(True)
    thread.start()

    try:
        yield acceptor
    finally:
        acceptor.stop()
        json_binding.stop()
        xml_binding.stop()


@contextmanager
def constructClient(log, bridge, ssl, type):
    sslctx = DEAFAULT_SSL_CONTEXT if ssl else None
    with constructAcceptor(log, ssl, bridge) as acceptor:
        client = None
        if type == "xml":
            xml_handler = [h for h in acceptor._handlers if h.NAME == type]
            for (method, name) in bridge.getBridgeMethods():
                xml_handler[0].xml_binding.server.register_function(method,
                                                                    name)
            client = create
        else:
            for handler in acceptor._handlers:
                if handler.NAME == type:
                    reactor = handler._reactor

        if not client:
            cpool = JsonRpcClientPool(reactor)
            t = threading.Thread(target=cpool.serve)
            t.setDaemon(True)
            t.start()
            client = cpool.createClient

        _, port = acceptor._socket.getsockname()
        clientFactory = partial(client, create_socket(sslctx, acceptor._host,
                                                      port))

        yield clientFactory


def create_socket(sslctx, host, port):
    sock = None
    if sslctx:
        sock = SSL.Connection(sslctx.context)
    else:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(TIMEOUT)
    sock.connect((host, port))
    return sock


def create(socket):
    return XMLClient(socket)


class XMLClient():
    def __init__(self, socket):
        self.socket = socket
        self.transport = CustomTransport(socket)

    def send(self, method, params):
        request = dumps(params, method)
        try:
            response = self.transport.request("localhost",
                                              "/RPC2", request)
        except Fault as e:
            response = e.faultString

        if isinstance(response, tuple):
            response = response[0]
        return response

    def connect(self):
        pass

    def setTimeout(self, timeout):
        self.socket.settimeout(timeout)

    def close(self):
        self.socket.close()


class CustomTransport(Transport):

    def __init__(self, socket):
        Transport.__init__(self)

        def connect(self):
            self.sock = socket

        connection = httplib.HTTPConnection
        connection.connect = connect
