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

import API
from contextlib import contextmanager
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

PERMUTATIONS = [[True], [False]]

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
                scheduler.stop(wait=False)


@contextmanager
def constructClient(log, bridge, ssl, dest=SUBSCRIPTION_ID_RESPONSE):
    sslctx = DEAFAULT_SSL_CONTEXT if ssl else None
    with constructAcceptor(log, ssl, bridge, dest) as acceptor:
        reactor = acceptor._handlers[0]._reactor

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
