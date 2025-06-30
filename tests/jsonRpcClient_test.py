# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division
import queue
import uuid
from contextlib import contextmanager

import yajsonrpc
from yajsonrpc.exception import JsonRpcInvalidRequestError
from testlib import VdsmTestCase


# Mocks yajsonrpc.ClientRpcTransportAdapter
class _TransportMock(object):
    def __init__(self):
        self._message_handler = lambda msg: None
        self._subs = {}
        self._sub_id_to_name = {}

    def set_message_handler(self, handler):
        self._message_handler = handler

    def send(self, data, destination=None):
        if destination in self._subs:
            self._subs[destination](data)

    def subscribe(self, queue_name, callback):
        sub_id = uuid.uuid4()
        self._sub_id_to_name[sub_id] = queue_name
        self._subs[queue_name] = callback
        return sub_id

    def unsubscribe(self, sub_id):
        name = self._sub_id_to_name[sub_id]
        del self._sub_id_to_name[sub_id]
        del self._subs[name]

    def close(self):
        pass


class _FakeEventSchema(object):
    def verify_event_params(self, *args, **kwargs):
        pass


class JsonRpcClientTests(VdsmTestCase):

    @contextmanager
    def _createClient(self):
        client = yajsonrpc.jsonrpcclient.JsonRpcClient(self.transport)
        try:
            yield client
        finally:
            client.close()

    def setUp(self):
        self.transport = _TransportMock()

    def test_notify(self):
        with self._createClient() as client:
            queue_name = "test.queue"
            event_queue = queue.Queue()

            client.subscribe(queue_name, event_queue)
            client.notify("test.event", queue_name,
                          _FakeEventSchema(), {"content": True})

            self.assertFalse(event_queue.empty())
            ev_id, ev_params = event_queue.get()
            self.assertEqual(ev_id, "test.event")
            self.assertEqual(ev_params['content'], True)

    def test_ignore_non_json_message(self):
        with self._createClient() as client:
            queue_name = "test.queue"
            event_queue = queue.Queue()

            client.subscribe(queue_name, event_queue)

            msg = "I am not a valid JSON message."
            self.transport.send(msg, queue_name)

            self.assertTrue(event_queue.empty())

    def test_invalid_event(self):
        with self._createClient() as client:
            queue_name = "test.queue"
            event_queue = queue.Queue()

            client.subscribe(queue_name, event_queue)

            msg = '{ "key": "value", "array": [2,1,3,4] }'
            with self.assertRaises(JsonRpcInvalidRequestError):
                self.transport.send(msg, queue_name)
