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
from collections import defaultdict

from xmlrpclib import Transport, dumps, Fault
from contextlib import contextmanager
from itertools import product
from rpc.bindingxmlrpc import BindingXMLRPC, XmlDetector
from yajsonrpc.betterAsyncore import Reactor
from yajsonrpc.stompreactor import StompDetector, StompRpcClient
from yajsonrpc.stomp import (
    LEGACY_SUBSCRIPTION_ID_REQUEST,
    LEGACY_SUBSCRIPTION_ID_RESPONSE
)
from yajsonrpc import Notification
from protocoldetector import MultiProtocolAcceptor
from rpc.bindingjsonrpc import BindingJsonRpc
from vdsm import utils
from sslhelper import DEAFAULT_SSL_CONTEXT

PERMUTATIONS = tuple(product((True, False), ("xml", "stomp")))

TIMEOUT = 3


class FakeClientIf(object):
    log = logging.getLogger("FakeClientIf")

    def __init__(self, binding):
        self.threadLocal = threading.local()
        self.json_binding = binding
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

    def notify(self, event_id, **kwargs):
        notification = Notification(
            event_id,
            self._send_notification,
        )
        notification.emit(**kwargs)

    def _send_notification(self, message):
        server = self.json_binding.reactor.server
        server.send(message, LEGACY_SUBSCRIPTION_ID_RESPONSE)


@contextmanager
def constructAcceptor(log, ssl, jsonBridge):
    sslctx = DEAFAULT_SSL_CONTEXT if ssl else None
    reactor = Reactor()
    acceptor = MultiProtocolAcceptor(
        reactor,
        "127.0.0.1",
        0,
        sslctx,
    )
    json_binding = BindingJsonRpc(jsonBridge, defaultdict(list), 60)
    json_binding.start()

    cif = FakeClientIf(json_binding)

    xml_binding = BindingXMLRPC(cif, cif.log)
    xml_binding.start()
    xmlDetector = XmlDetector(xml_binding)
    acceptor.add_detector(xmlDetector)

    jsonBridge.cif = cif

    stompDetector = StompDetector(json_binding)
    acceptor.add_detector(stompDetector)

    thread = threading.Thread(target=reactor.process_requests,
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
            client = XMLClient(socket)
        else:
            for handler in acceptor._handlers:
                if handler.NAME == type:
                    reactor = handler._reactor

            def client(client_socket):
                return StompRpcClient(
                    reactor.createClient(client_socket),
                    LEGACY_SUBSCRIPTION_ID_REQUEST,
                    LEGACY_SUBSCRIPTION_ID_RESPONSE,
                )

        def clientFactory():
            return client(utils.create_connected_socket(
                acceptor._host,
                acceptor._port,
                sslctx=sslctx,
                timeout=TIMEOUT
            ))

        yield clientFactory


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
