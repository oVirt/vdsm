# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division
import logging
import threading
from collections import defaultdict

from contextlib import contextmanager
from vdsm.common import time
from yajsonrpc.betterAsyncore import Reactor
from yajsonrpc.stompclient import StompRpcClient
from yajsonrpc.stompserver import StompDetector
from yajsonrpc.stomp import (
    SUBSCRIPTION_ID_REQUEST,
    SUBSCRIPTION_ID_RESPONSE
)
from yajsonrpc import Notification
from vdsm.rpc.bindingjsonrpc import BindingJsonRpc
from vdsm.protocoldetector import MultiProtocolAcceptor
from vdsm import API
from vdsm import schedule
from vdsm import utils

from monkeypatch import MonkeyPatchScope

from testlib import ipv6_enabled


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
        from vdsm import API
        API.Image.BLANK_UUID = '00000000-0000-0000-0000-000000000000'
        API.StorageDomain.BLANK_UUID = '00000000-0000-0000-0000-000000000000'
        API.Volume.BLANK_UUID = "00000000-0000-0000-0000-000000000000"

    @property
    def ready(self):
        return True

    def notify(self, event_id, params=None, destination=None):
        if not params:
            params = {}

        if destination is None:
            destination = self.dest

        server = self.json_binding.reactor.server

        notification = Notification(
            event_id,
            lambda message: server.send(message, destination),
            self.json_binding.bridge.event_schema
        )
        notification.emit(params)


@contextmanager
def constructAcceptor(log, ssl_ctx, jsonBridge,
                      dest=SUBSCRIPTION_ID_RESPONSE):
    reactor = Reactor()
    acceptor = MultiProtocolAcceptor(
        reactor,
        "::1" if ipv6_enabled() else "127.0.0.1",
        0,
        ssl_ctx,
    )

    scheduler = schedule.Scheduler(name="test.Scheduler",
                                   clock=time.monotonic_time)
    scheduler.start()

    cif = FakeClientIf(dest)

    json_binding = BindingJsonRpc(jsonBridge, defaultdict(list), 60,
                                  scheduler, cif)
    json_binding.start()

    cif.json_binding = json_binding

    with MonkeyPatchScope([
        (API.clientIF, 'getInstance', lambda _: cif),
        (API, 'confirm_connectivity', lambda: None)
    ]):
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
def constructClient(log, bridge, ssl_ctx, dest=SUBSCRIPTION_ID_RESPONSE):
    with constructAcceptor(log, ssl_ctx, bridge, dest) as acceptor:
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
                sslctx=ssl_ctx,
                timeout=TIMEOUT
            ))

        yield clientFactory
