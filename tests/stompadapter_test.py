# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division
from collections import defaultdict

from testlib import VdsmTestCase as TestCaseBase
from yajsonrpc import JsonRpcRequest
from yajsonrpc.betterAsyncore import Reactor
from yajsonrpc.stomp import \
    Command, \
    Frame, \
    Headers, \
    SUBSCRIPTION_ID_REQUEST
from yajsonrpc.stomp import AsyncDispatcher
from yajsonrpc.stompserver import StompAdapterImpl
from stomp_test_utils import (
    FakeAsyncClient,
    FakeAsyncDispatcher,
    FakeConnection,
    FakeSubscription
)


class ConnectFrameTest(TestCaseBase):

    def test_connect(self):
        frame = Frame(Command.CONNECT,
                      {Headers.ACCEPT_VERSION: '1.2',
                       Headers.HEARTBEAT: '0,8000'})

        adapter = StompAdapterImpl(Reactor(), defaultdict(list), {})
        adapter.handle_frame(FakeAsyncDispatcher(adapter), frame)

        resp_frame = adapter.pop_message()
        self.assertEqual(resp_frame.command, Command.CONNECTED)
        self.assertEqual(resp_frame.headers['version'], '1.2')
        self.assertEqual(resp_frame.headers[Headers.HEARTBEAT], '8000,0')

    def test_min_heartbeat(self):
        frame = Frame(Command.CONNECT,
                      {Headers.ACCEPT_VERSION: '1.2',
                       Headers.HEARTBEAT: '0,500'})

        adapter = StompAdapterImpl(Reactor(), defaultdict(list), {})
        adapter.handle_frame(FakeAsyncDispatcher(adapter), frame)

        resp_frame = adapter.pop_message()
        self.assertEqual(resp_frame.command, Command.CONNECTED)
        self.assertEqual(resp_frame.headers['version'], '1.2')
        self.assertEqual(resp_frame.headers[Headers.HEARTBEAT], '1000,0')

    def test_incoming_heartbeat(self):
        frame = Frame(Command.CONNECT,
                      {Headers.ACCEPT_VERSION: '1.2',
                       Headers.HEARTBEAT: '6000,5000'})

        adapter = StompAdapterImpl(Reactor(), defaultdict(list), {})
        dispatcher = AsyncDispatcher(FakeConnection(adapter), adapter)
        adapter.handle_frame(dispatcher, frame)

        resp_frame = adapter.pop_message()
        self.assertEqual(resp_frame.command, Command.CONNECTED)
        self.assertEqual(resp_frame.headers['version'], '1.2')
        self.assertEqual(resp_frame.headers[Headers.HEARTBEAT], '5000,6000')
        self.assertEqual(dispatcher._incoming_heartbeat_in_milis, 6000)
        self.assertEqual(dispatcher._outgoing_heartbeat_in_milis, 5000)

    def test_unsuported_version(self):
        frame = Frame(Command.CONNECT,
                      {Headers.ACCEPT_VERSION: '1.0'})

        adapter = StompAdapterImpl(Reactor(), defaultdict(list), {})
        adapter.handle_frame(FakeAsyncDispatcher(adapter), frame)

        resp_frame = adapter.pop_message()
        self.assertEqual(resp_frame.command, Command.ERROR)
        self.assertEqual(resp_frame.body, b'Version unsupported')

    def test_no_heartbeat(self):
        frame = Frame(Command.CONNECT,
                      {Headers.ACCEPT_VERSION: '1.2'})

        adapter = StompAdapterImpl(Reactor(), defaultdict(list), {})
        adapter.handle_frame(FakeAsyncDispatcher(adapter), frame)

        resp_frame = adapter.pop_message()
        self.assertEqual(resp_frame.command, Command.CONNECTED)
        self.assertEqual(resp_frame.headers['version'], '1.2')
        self.assertEqual(resp_frame.headers[Headers.HEARTBEAT], '0,0')

    def test_no_headers(self):
        frame = Frame(Command.CONNECT)

        adapter = StompAdapterImpl(Reactor(), defaultdict(list), {})
        adapter.handle_frame(FakeAsyncDispatcher(adapter), frame)

        resp_frame = adapter.pop_message()
        self.assertEqual(resp_frame.command, Command.ERROR)
        self.assertEqual(resp_frame.body, b'Version unsupported')


class SubscriptionFrameTest(TestCaseBase):

    def test_subscribe(self):
        frame = Frame(Command.SUBSCRIBE,
                      {Headers.DESTINATION: 'jms.queue.events',
                       'ack': 'auto',
                       'id': 'ad052acb-a934-4e10-8ec3-00c7417ef8d1'})

        destinations = defaultdict(list)

        adapter = StompAdapterImpl(Reactor(), destinations, {})
        adapter.handle_frame(FakeAsyncDispatcher(adapter), frame)

        subscription = destinations['jms.queue.events'][0]
        self.assertEqual(subscription.id,
                         'ad052acb-a934-4e10-8ec3-00c7417ef8d1')
        self.assertEqual(subscription.destination,
                         'jms.queue.events')

    def test_no_destination(self):
        frame = Frame(Command.SUBSCRIBE,
                      {'ack': 'auto',
                       'id': 'ad052acb-a934-4e10-8ec3-00c7417ef8d1'})

        adapter = StompAdapterImpl(Reactor(), defaultdict(list), {})
        adapter.handle_frame(FakeAsyncDispatcher(adapter), frame)

        resp_frame = adapter.pop_message()
        self.assertEqual(resp_frame.command, Command.ERROR)
        self.assertEqual(resp_frame.body,
                         b'Missing destination or subscription id header')

    def test_no_id(self):
        frame = Frame(Command.SUBSCRIBE,
                      {'ack': 'auto',
                       Headers.DESTINATION: 'jms.queue.events'})

        adapter = StompAdapterImpl(Reactor(), defaultdict(list), {})
        adapter.handle_frame(FakeAsyncDispatcher(adapter), frame)

        resp_frame = adapter.pop_message()
        self.assertEqual(resp_frame.command, Command.ERROR)
        self.assertEqual(resp_frame.body,
                         b'Missing destination or subscription id header')


class UnsubscribeFrameTest(TestCaseBase):

    def test_unsubscribe(self):
        frame = Frame(Command.UNSUBSCRIBE,
                      {'id': 'ad052acb-a934-4e10-8ec3-00c7417ef8d1'})

        subscription = FakeSubscription('jms.queue.events',
                                        'ad052acb-a934-4e10-8ec3-00c7417ef8d1')

        destinations = defaultdict(list)
        destinations['jms.queue.events'].append(subscription)

        adapter = StompAdapterImpl(Reactor(), destinations, {})
        adapter._sub_ids['ad052acb-a934-4e10-8ec3-00c7417ef8d1'] = subscription
        adapter.handle_frame(FakeAsyncDispatcher(adapter), frame)

        self.assertEqual(len(adapter._sub_ids), 0)
        self.assertEqual(len(destinations), 0)

    def test_multipe_subscriptions(self):
        frame = Frame(Command.UNSUBSCRIBE,
                      {'id': 'ad052acb-a934-4e10-8ec3-00c7417ef8d1'})

        subscription = FakeSubscription('jms.queue.events',
                                        'ad052acb-a934-4e10-8ec3-00c7417ef8d1')
        subscription2 = FakeSubscription('jms.queue.events',
                                         'e8a93a6-d886-4cfa-97b9-2d54209053ff')

        destinations = defaultdict(list)
        destinations['jms.queue.events'].append(subscription)
        destinations['jms.queue.events'].append(subscription2)

        adapter = StompAdapterImpl(Reactor(), destinations, {})
        adapter._sub_ids['ad052acb-a934-4e10-8ec3-00c7417ef8d1'] = subscription
        adapter.handle_frame(FakeAsyncDispatcher(adapter), frame)

        self.assertEqual(len(adapter._sub_ids), 0)
        self.assertEqual(len(destinations), 1)
        self.assertEqual(destinations['jms.queue.events'], [subscription2])

    def test_no_id(self):
        frame = Frame(Command.UNSUBSCRIBE)

        adapter = StompAdapterImpl(Reactor(), defaultdict(list), {})
        adapter.handle_frame(FakeAsyncDispatcher(adapter), frame)

        resp_frame = adapter.pop_message()
        self.assertEqual(resp_frame.command, Command.ERROR)
        self.assertEqual(resp_frame.body, b'Missing id header')


class SendFrameTest(TestCaseBase):

    def test_send(self):
        frame = Frame(command=Command.SEND,
                      headers={Headers.DESTINATION: 'jms.topic.vdsm_requests',
                               Headers.REPLY_TO: 'jms.topic.vdsm_responses',
                               Headers.CONTENT_LENGTH: '103'},
                      body=('{"jsonrpc":"2.0","method":"Host.getAllVmStats",'
                            '"params":{},"id":"e8a936a6-d886-4cfa-97b9-2d54209'
                            '053ff"}'
                            )
                      )

        ids = {}

        adapter = StompAdapterImpl(Reactor(), defaultdict(list), ids)
        adapter.handle_frame(FakeAsyncDispatcher(adapter), frame)

        data = adapter.pop_message()
        self.assertIsNot(data, None)
        request = JsonRpcRequest.decode(data)
        self.assertEqual(request.method, 'Host.getAllVmStats')
        self.assertEqual(len(ids), 1)

    def test_send_legacy(self):
        dest = SUBSCRIPTION_ID_REQUEST
        frame = Frame(command=Command.SEND,
                      headers={Headers.DESTINATION: dest,
                               Headers.REPLY_TO: 'jms.topic.vdsm_responses',
                               Headers.CONTENT_LENGTH: '103'},
                      body=('{"jsonrpc":"2.0","method":"Host.getAllVmStats",'
                            '"params":{},"id":"e8a936a6-d886-4cfa-97b9-2d54209'
                            '053ff"}'
                            )
                      )

        ids = {}

        adapter = StompAdapterImpl(Reactor(), defaultdict(list), ids)
        adapter.handle_frame(FakeAsyncDispatcher(adapter), frame)

        data = adapter.pop_message()
        self.assertIsNot(data, None)
        request = JsonRpcRequest.decode(data)
        self.assertEqual(request.method, 'Host.getAllVmStats')
        self.assertEqual(len(ids), 1)

    def test_send_no_destination(self):
        frame = Frame(Command.SEND,
                      {Headers.DESTINATION: 'jms.topic.unknown'})

        adapter = StompAdapterImpl(Reactor(), defaultdict(list), {})
        adapter.handle_frame(FakeAsyncDispatcher(adapter), frame)

        resp_frame = adapter.pop_message()
        self.assertEqual(resp_frame.command, Command.ERROR)
        self.assertEqual(resp_frame.body, b'Subscription not available')

    def test_send_batch(self):
        body = ('[{"jsonrpc":"2.0","method":"Host.getAllVmStats","params":{},'
                '"id":"e8a936a6-d886-4cfa-97b9-2d54209053ff"},'
                '{"jsonrpc":"2.0","method":"Host.getAllVmStats","params":{},'
                '"id":"1202b274-5a06-4671-8b13-1d2715429668"}]')

        frame = Frame(command=Command.SEND,
                      headers={Headers.DESTINATION: 'jms.topic.vdsm_requests',
                               Headers.REPLY_TO: 'jms.topic.vdsm_responses',
                               Headers.CONTENT_LENGTH: '209'},
                      body=body
                      )

        ids = {}

        adapter = StompAdapterImpl(Reactor(), defaultdict(list), ids)
        adapter.handle_frame(FakeAsyncDispatcher(adapter), frame)

        data = adapter.pop_message()
        self.assertIsNot(data, None)
        self.assertEqual(len(ids), 2)

    def test_send_broker(self):
        frame = Frame(command=Command.SEND,
                      headers={Headers.DESTINATION: 'jms.topic.destination',
                               Headers.CONTENT_LENGTH: '103'},
                      body=('{"jsonrpc":"2.0","method":"Host.getAllVmStats",'
                            '"params":{},"id":"e8a936a6-d886-4cfa-97b9-2d54209'
                            '053ff"}'
                            )
                      )

        subscription = FakeSubscription('jms.topic.destination',
                                        'e8a936a6-d886-4cfa-97b9-2d54209053ff')

        destinations = defaultdict(list)
        destinations['jms.topic.destination'].append(subscription)

        adapter = StompAdapterImpl(Reactor(), destinations, {})
        subscription.set_client(adapter)
        adapter.handle_frame(FakeAsyncDispatcher(adapter), frame)

        resp_frame = adapter.pop_message()
        self.assertEqual(resp_frame.command, Command.MESSAGE)

    def test_send_internal_and_broker(self):
        frame = Frame(command=Command.SEND,
                      headers={Headers.DESTINATION: 'jms.topic.vdsm_requests',
                               Headers.REPLY_TO: 'jms.topic.vdsm_responses',
                               Headers.CONTENT_LENGTH: '103'},
                      body=('{"jsonrpc":"2.0","method":"Host.getAllVmStats",'
                            '"params":{},"id":"e8a936a6-d886-4cfa-97b9-2d54209'
                            '053ff"}'
                            )
                      )

        ids = {}

        subscription = FakeSubscription('jms.topic.vdsm_requests',
                                        'e8a936a6-d886-4cfa-97b9-2d54209053ff')
        client = FakeAsyncClient()
        subscription.set_client(client)

        destinations = defaultdict(list)
        destinations['jms.topic.vdsm_requests'].append(subscription)

        adapter = StompAdapterImpl(Reactor(), destinations, ids)
        adapter.handle_frame(FakeAsyncDispatcher(adapter), frame)

        data = adapter.pop_message()
        self.assertIsNot(data, None)
        request = JsonRpcRequest.decode(data)
        self.assertEqual(request.method, 'Host.getAllVmStats')
        self.assertEqual(len(ids), 1)

        resp_frame = client.pop_message()
        self.assertIsNot(resp_frame, None)
        self.assertEqual(resp_frame.command, Command.MESSAGE)
        self.assertEqual(resp_frame.body, data)

    def test_send_broker_parent_topic(self):
        frame = Frame(command=Command.SEND,
                      headers={Headers.DESTINATION:
                               'jms.topic.vdsm_requests.getAllVmStats',
                               Headers.REPLY_TO:
                               'jms.topic.vdsm_responses.getAllVmStats',
                               Headers.CONTENT_LENGTH: '103'},
                      body=('{"jsonrpc":"2.0","method":"Host.getAllVmStats",'
                            '"params":{},"id":"e8a936a6-d886-4cfa-97b9-2d54209'
                            '053ff"}'
                            )
                      )

        ids = {}

        subscription = FakeSubscription('jms.topic.vdsm_requests',
                                        'e8a936a6-d886-4cfa-97b9-2d54209053ff')
        client = FakeAsyncClient()
        subscription.set_client(client)

        destinations = defaultdict(list)
        destinations['jms'].append(subscription)
        destinations['jms.topic'].append(subscription)
        destinations['jms.topic.vdsm_requests'].append(subscription)

        # Topics that should not match
        destinations['jms.other'].append(subscription)
        destinations['jms.top'].append(subscription)

        adapter = StompAdapterImpl(Reactor(), destinations, ids)
        adapter.handle_frame(FakeAsyncDispatcher(adapter), frame)

        data = adapter.pop_message()
        self.assertIsNot(data, None)
        request = JsonRpcRequest.decode(data)
        self.assertEqual(request.method, 'Host.getAllVmStats')
        self.assertEqual(len(ids), 1)

        for i in range(3):
            resp_frame = client.pop_message()
            self.assertIsNot(resp_frame, None)
            self.assertEqual(resp_frame.command, Command.MESSAGE)
            self.assertEqual(resp_frame.body, data)

        # The last two subscriptions do not match
        self.assertTrue(client.empty())

    def test_flow_header(self):
        frame = Frame(command=Command.SEND,
                      headers={Headers.DESTINATION: SUBSCRIPTION_ID_REQUEST,
                               Headers.FLOW_ID: 'e8a936a6',
                               Headers.REPLY_TO: 'jms.topic.vdsm_responses',
                               Headers.CONTENT_LENGTH: '103'},
                      body=('{"jsonrpc":"2.0","method":"Host.getAllVmStats",'
                            '"params":{},"id":"e8a936a6-d886-4cfa-97b9-2d54209'
                            '053ff"}'
                            )
                      )

        adapter = StompAdapterImpl(Reactor(), defaultdict(list), {})
        dispatcher = FakeAsyncDispatcher(adapter)
        adapter.handle_frame(dispatcher, frame)

        self.assertEqual(dispatcher.connection.flow_id, 'e8a936a6')

    def test_no_flow_header(self):
        frame = Frame(command=Command.SEND,
                      headers={Headers.DESTINATION: SUBSCRIPTION_ID_REQUEST,
                               Headers.REPLY_TO: 'jms.topic.vdsm_responses',
                               Headers.CONTENT_LENGTH: '103'},
                      body=('{"jsonrpc":"2.0","method":"Host.getAllVmStats",'
                            '"params":{},"id":"e8a936a6-d886-4cfa-97b9-2d54209'
                            '053ff"}'
                            )
                      )

        adapter = StompAdapterImpl(Reactor(), defaultdict(list), {})
        dispatcher = FakeAsyncDispatcher(adapter)
        adapter.handle_frame(dispatcher, frame)

        self.assertIsNone(dispatcher.connection.flow_id)


class ServerTimeoutTests(TestCaseBase):
    def test_no_subscriptions(self):
        adapter = StompAdapterImpl(Reactor(), defaultdict(list), {})
        dispatcher = AsyncDispatcher(FakeConnection(adapter), adapter)

        frame1 = Frame(Command.SUBSCRIBE,
                       {Headers.DESTINATION: 'jms.queue.events',
                        'ack': 'auto',
                        'id': 'ad052acb-a934-4e10-8ec3-00c7417ef8d1'})

        frame2 = Frame(Command.SUBSCRIBE,
                       {Headers.DESTINATION: 'jms.queue.events',
                        'ack': 'auto',
                        'id': 'ad052acb-a934-4e10-8ec3-00c7417ef8d2'})

        destinations = defaultdict(list)

        adapter = StompAdapterImpl(Reactor(), destinations, {})
        adapter.handle_frame(dispatcher, frame1)
        adapter.handle_frame(dispatcher, frame2)

        adapter.handle_timeout(dispatcher)

        self.assertEqual(len(adapter._sub_ids), 0)
        self.assertEqual(len(destinations), 0)
