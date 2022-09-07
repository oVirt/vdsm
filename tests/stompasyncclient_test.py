# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division
from uuid import uuid4

from testlib import VdsmTestCase as TestCaseBase

from yajsonrpc import stompclient

from yajsonrpc.stompclient import \
    AsyncClient, \
    Command, \
    Frame, \
    Headers, \
    StompError
from stomp_test_utils import FakeSubscription, FakeAsyncDispatcher
from monkeypatch import MonkeyPatchScope


class AsyncClientTest(TestCaseBase):

    def test_connect(self):
        client = AsyncClient()
        client.handle_connect()

        req_frame = client.pop_message()

        self.assertEqual(req_frame.command, Command.CONNECT)
        self.assertEqual(req_frame.headers[Headers.ACCEPT_VERSION], '1.2')
        self.assertEqual(req_frame.headers[Headers.HEARTBEAT], '0,24000')

    def test_set_heartbeat(self):
        client = AsyncClient(incoming_heartbeat=200, outgoing_heartbeat=100)
        client.handle_connect()

        req_frame = client.pop_message()

        self.assertEqual(req_frame.command, Command.CONNECT)
        self.assertEqual(req_frame.headers[Headers.ACCEPT_VERSION], '1.2')
        self.assertEqual(req_frame.headers[Headers.HEARTBEAT], '120,160')

    def test_subscribe(self):
        client = AsyncClient()

        id = str(uuid4())
        client.subscribe(destination='jms.queue.events', ack='client',
                         sub_id=id)

        req_frame = client.pop_message()
        self.assertEqual(len(client._subscriptions), 1)
        self.assertEqual(req_frame.command, Command.SUBSCRIBE)
        self.assertEqual(req_frame.headers['destination'], 'jms.queue.events')
        self.assertEqual(req_frame.headers['id'], id)
        self.assertEqual(req_frame.headers['ack'], 'client')

    def test_manage_subscription(self):
        client = AsyncClient()

        subscription = client.subscribe(destination='jms.queue.events',
                                        ack='client',
                                        sub_id=str(uuid4()))
        client.unsubscribe(subscription)
        self.assertEqual(len(client._subscriptions), 0)

    def test_restore_subcsriptions(self):
        client = AsyncClient()
        client.subscribe(destination='jms.queue.events', ack='client',
                         sub_id=str(uuid4()))
        client.subscribe(destination='jms.queue.events', ack='client',
                         sub_id=str(uuid4()))
        client.subscribe(destination='jms.queue.events', ack='client',
                         sub_id=str(uuid4()))

        client.restore_subscriptions()
        self.assertEqual(len(client._subscriptions), 3)

    def test_unsubscribe_with_different_id(self):
        client = AsyncClient()

        client.subscribe(destination='jms.queue.events',
                         ack='client-individual',
                         sub_id=str(uuid4()))
        # ignore subscribe frame
        client.pop_message()

        client.unsubscribe(FakeSubscription('jms.queue.events',
                                            'ad052acb-a934-4e10-8ec3'))

        self.assertEqual(len(client._subscriptions), 1)
        self.assertFalse(client.has_outgoing_messages)

    def test_send(self):
        client = AsyncClient()
        data = (b'{"jsonrpc":"2.0","method":"Host.getAllVmStats","params":{},'
                b'"id":"e8a936a6-d886-4cfa-97b9-2d54209053ff"}')
        headers = {Headers.REPLY_TO: 'jms.topic.vdsm_responses',
                   Headers.CONTENT_LENGTH: '103'}
        # make sure that client can send messages
        client._connected.set()

        client.send('jms.topic.vdsm_requests', data, headers)

        req_frame = client.pop_message()
        self.assertEqual(req_frame.command, Command.SEND)
        self.assertEqual(req_frame.headers['destination'],
                         'jms.topic.vdsm_requests')
        self.assertEqual(req_frame.headers[Headers.REPLY_TO],
                         'jms.topic.vdsm_responses')
        self.assertEqual(req_frame.body, data)

    def test_resend(self):
        client = AsyncClient()

        data = (b'{"jsonrpc":"2.0","method":"Host.getAllVmStats","params":{},'
                b'"id":"e8a936a6-d886-4cfa-97b9-2d54209053ff"}')
        headers = {Headers.REPLY_TO: 'jms.topic.vdsm_responses',
                   Headers.CONTENT_LENGTH: '103'}

        with MonkeyPatchScope([(stompclient, 'CALL_TIMEOUT', 0.5)]):
            client.send('jms.topic.vdsm_requests', data, headers)
            client._connected.set()
            req_frame = client.pop_message()
            self.assertEqual(req_frame.command, Command.SEND)
            self.assertEqual(req_frame.headers['destination'],
                             'jms.topic.vdsm_requests')
            self.assertEqual(req_frame.headers[Headers.REPLY_TO],
                             'jms.topic.vdsm_responses')
            self.assertEqual(req_frame.body, data)

    def test_receive_connected(self):
        client = AsyncClient()
        frame = Frame(Command.CONNECTED,
                      {'version': '1.2', Headers.HEARTBEAT: '8000,0'})

        client.handle_frame(FakeAsyncDispatcher(''), frame)

        self.assertTrue(client.connected)

    def test_receive_message(self):
        client = AsyncClient()
        id = 'ad052acb-a934-4e10-8ec3-00c7417ef8d1'
        headers = {Headers.CONTENT_LENGTH: '78',
                   Headers.DESTINATION: 'jms.topic.vdsm_responses',
                   Headers.CONTENT_TYPE: 'application/json',
                   Headers.SUBSCRIPTION: id}
        body = ('{"jsonrpc": "2.0", "id": "e8a936a6-d886-4cfa-97b9-2d54209053f'
                'f", "result": []}')
        frame = Frame(command=Command.MESSAGE, headers=headers, body=body)

        def message_handler(sub, frame):
            client.queue_frame(frame)

        client.subscribe('', 'auto', id, message_handler)
        # ignore subscribe frame
        client.pop_message()

        client.handle_frame(None, frame)

        self.assertEqual(frame, client.pop_message())

    def test_receive_error(self):
        client = AsyncClient()
        frame = Frame(command=Command.ERROR, body='Test error')

        with self.assertRaises(StompError):
            client.handle_frame(None, frame)
