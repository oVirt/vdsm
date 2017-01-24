#
# Copyright 2014-2017 Red Hat, Inc.
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
import logging
import os.path
import threading
from collections import defaultdict

import six.moves.http_client

import API
from six.moves.xmlrpc_client import Transport, dumps, Fault
from contextlib import contextmanager
from itertools import product
from vdsm.rpc.bindingxmlrpc import BindingXMLRPC, XmlDetector
from yajsonrpc.betterAsyncore import Reactor
from yajsonrpc.stompreactor import StompDetector, StompRpcClient
from yajsonrpc.stomp import (
    SUBSCRIPTION_ID_REQUEST,
    SUBSCRIPTION_ID_RESPONSE
)
from yajsonrpc import Notification
from vdsm.config import config
from vdsm.rpc.bindingjsonrpc import BindingJsonRpc
from vdsm.protocoldetector import MultiProtocolAcceptor
from vdsm import constants
from vdsm import schedule
from vdsm import utils

from testlib import namedTemporaryDir
from monkeypatch import MonkeyPatchScope

if config.get('vars', 'ssl_implementation') == 'm2c':
    from integration.m2chelper import DEAFAULT_SSL_CONTEXT
else:
    from integration.sslhelper import DEAFAULT_SSL_CONTEXT

PERMUTATIONS = tuple(product((True, False), ("xml", "stomp")))

TIMEOUT = 3


class FakeClientIf(object):
    log = logging.getLogger("FakeClientIf")

    def __init__(self, dest):
        self.threadLocal = threading.local()
        self.dest = dest
        self.irs = True
        self.gluster = None
        self.json_binding = None

        # API module is redefined for apiTests so we need to add BLANK_UUIDs
        import API
        API.Image.BLANK_UUID = '00000000-0000-0000-0000-000000000000'
        API.StorageDomain.BLANK_UUID = '00000000-0000-0000-0000-000000000000'
        API.Volume.BLANK_UUID = "00000000-0000-0000-0000-000000000000"

    @property
    def ready(self):
        return True

    def notify(self, event_id, params=None):
        if not params:
            params = {}

        notification = Notification(
            event_id,
            self._send_notification,
            self.json_binding.bridge.event_schema
        )
        notification.emit(params)

    def _send_notification(self, message):
        server = self.json_binding.reactor.server
        server.send(message, self.dest)


@contextmanager
def constructAcceptor(log, ssl, jsonBridge,
                      dest=SUBSCRIPTION_ID_RESPONSE):
    sslctx = DEAFAULT_SSL_CONTEXT if ssl else None
    reactor = Reactor()
    acceptor = MultiProtocolAcceptor(
        reactor,
        "::1",
        0,
        sslctx,
    )

    scheduler = schedule.Scheduler(name="test.Scheduler",
                                   clock=utils.monotonic_time)
    scheduler.start()

    cif = FakeClientIf(dest)

    json_binding = BindingJsonRpc(jsonBridge, defaultdict(list), 60,
                                  scheduler, cif)
    json_binding.start()

    cif.json_binding = json_binding

    with namedTemporaryDir() as tmp_dir:
        client_log = os.path.join(tmp_dir, 'client.log')
        with MonkeyPatchScope([(API.clientIF, 'getInstance', lambda _: cif),
                              (constants, 'P_VDSM_CLIENT_LOG', client_log)]):
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
                scheduler.stop(wait=False)


@contextmanager
def constructClient(log, bridge, ssl, type, dest=SUBSCRIPTION_ID_RESPONSE):
    sslctx = DEAFAULT_SSL_CONTEXT if ssl else None
    with constructAcceptor(log, ssl, bridge, dest) as acceptor:
        client = None
        if type == "xml":
            xml_handler = [h for h in acceptor._handlers if h.NAME == type]
            for (method, name) in bridge.getBridgeMethods():
                xml_handler[0].xml_binding.server.register_function(method,
                                                                    name)
            client = XMLClient
        else:
            for handler in acceptor._handlers:
                if handler.NAME == type:
                    reactor = handler._reactor

            def client(client_socket):
                return StompRpcClient(
                    reactor.createClient(client_socket),
                    SUBSCRIPTION_ID_REQUEST,
                    SUBSCRIPTION_ID_RESPONSE,
                )

        def clientFactory():
            return client(utils.create_connected_socket(
                acceptor._host,
                acceptor._port,
                sslctx=sslctx,
                timeout=TIMEOUT
            ))

        yield clientFactory


class XMLClient(object):
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

        connection = six.moves.http_client.HTTPConnection
        connection.connect = connect
