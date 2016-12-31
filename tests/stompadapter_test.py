#
# Copyright 2015 Red Hat, Inc.
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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#
from collections import defaultdict

from testlib import VdsmTestCase as TestCaseBase
from yajsonrpc import JsonRpcRequest
from yajsonrpc.betterAsyncore import Reactor
from yajsonrpc.stomp import \
    Command, \
    Frame, \
    Headers, \
    SUBSCRIPTION_ID_REQUEST
from yajsonrpc.stompreactor import StompAdapterImpl


class TestConnection(object):

    def __init__(self, client):
        self._client = client

    def send_raw(self, msg):
        self._client.queue_frame(msg)

    def handleMessage(self, data):
        self._client.queue_frame(data)


class TestDispatcher(object):

    def __init__(self, client):
        self._client = client

    def setHeartBeat(self, outgoing, incoming=0):
        pass

    @property
    def connection(self):
        return TestConnection(self._client)


class TestSubscription(object):

    def __init__(self, destination, id):
        self._destination = destination
        self._id = id

    def set_client(self, client):
        self._client = TestConnection(client)

    @property
    def destination(self):
        return self._destination

    @property
    def id(self):
        return self._id

    @property
    def client(self):
        return self._client


class ConnectFrameTest(TestCaseBase):

    def test_connect(self):
        frame = Frame(Command.CONNECT,
                      {Headers.ACCEPT_VERSION: '1.2',
                       Headers.HEARTEBEAT: '0,8000'})

        adapter = StompAdapterImpl(Reactor(), defaultdict(list), {})
        adapter.handle_frame(TestDispatcher(adapter), frame)

        resp_frame = adapter.pop_message()
        self.assertEqual(resp_frame.command, Command.CONNECTED)
        self.assertEqual(resp_frame.headers['version'], '1.2')
        self.assertEqual(resp_frame.headers[Headers.HEARTEBEAT], '8000,0')

    def test_min_heartbeat(self):
        frame = Frame(Command.CONNECT,
                      {Headers.ACCEPT_VERSION: '1.2',
                       Headers.HEARTEBEAT: '0,500'})

        adapter = StompAdapterImpl(Reactor(), defaultdict(list), {})
        adapter.handle_frame(TestDispatcher(adapter), frame)

        resp_frame = adapter.pop_message()
        self.assertEqual(resp_frame.command, Command.CONNECTED)
        self.assertEqual(resp_frame.headers['version'], '1.2')
        self.assertEqual(resp_frame.headers[Headers.HEARTEBEAT], '1000,0')

    def test_unsuported_version(self):
        frame = Frame(Command.CONNECT,
                      {Headers.ACCEPT_VERSION: '1.0'})

        adapter = StompAdapterImpl(Reactor(), defaultdict(list), {})
        adapter.handle_frame(TestDispatcher(adapter), frame)

        resp_frame = adapter.pop_message()
        self.assertEqual(resp_frame.command, Command.ERROR)
        self.assertEqual(resp_frame.body, 'Version unsupported')

    def test_no_heartbeat(self):
        frame = Frame(Command.CONNECT,
                      {Headers.ACCEPT_VERSION: '1.2'})

        adapter = StompAdapterImpl(Reactor(), defaultdict(list), {})
        adapter.handle_frame(TestDispatcher(adapter), frame)

        resp_frame = adapter.pop_message()
        self.assertEqual(resp_frame.command, Command.CONNECTED)
        self.assertEqual(resp_frame.headers['version'], '1.2')
        self.assertEqual(resp_frame.headers[Headers.HEARTEBEAT], '0,0')

    def test_no_headers(self):
        frame = Frame(Command.CONNECT)

        adapter = StompAdapterImpl(Reactor(), defaultdict(list), {})
        adapter.handle_frame(TestDispatcher(adapter), frame)

        resp_frame = adapter.pop_message()
        self.assertEqual(resp_frame.command, Command.ERROR)
        self.assertEqual(resp_frame.body, 'Version unsupported')


class SubscriptionFrameTest(TestCaseBase):

    def test_subscribe(self):
        frame = Frame(Command.SUBSCRIBE,
                      {Headers.DESTINATION: 'jms.queue.events',
                       'ack': 'auto',
                       'id': 'ad052acb-a934-4e10-8ec3-00c7417ef8d1'})

        destinations = defaultdict(list)

        adapter = StompAdapterImpl(Reactor(), destinations, {})
        adapter.handle_frame(TestDispatcher(adapter), frame)

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
        adapter.handle_frame(TestDispatcher(adapter), frame)

        resp_frame = adapter.pop_message()
        self.assertEqual(resp_frame.command, Command.ERROR)
        self.assertEqual(resp_frame.body,
                         'Missing destination or subscription id header')

    def test_no_id(self):
        frame = Frame(Command.SUBSCRIBE,
                      {'ack': 'auto',
                       Headers.DESTINATION: 'jms.queue.events'})

        adapter = StompAdapterImpl(Reactor(), defaultdict(list), {})
        adapter.handle_frame(TestDispatcher(adapter), frame)

        resp_frame = adapter.pop_message()
        self.assertEqual(resp_frame.command, Command.ERROR)
        self.assertEqual(resp_frame.body,
                         'Missing destination or subscription id header')


class UnsubscribeFrameTest(TestCaseBase):

    def test_unsubscribe(self):
        frame = Frame(Command.UNSUBSCRIBE,
                      {'id': 'ad052acb-a934-4e10-8ec3-00c7417ef8d1'})

        subscription = TestSubscription('jms.queue.events',
                                        'ad052acb-a934-4e10-8ec3-00c7417ef8d1')

        destinations = defaultdict(list)
        destinations['jms.queue.events'].append(subscription)

        adapter = StompAdapterImpl(Reactor(), destinations, {})
        adapter._sub_ids['ad052acb-a934-4e10-8ec3-00c7417ef8d1'] = subscription
        adapter.handle_frame(TestDispatcher(adapter), frame)

        self.assertEqual(len(adapter._sub_ids), 0)
        self.assertEqual(len(destinations), 0)

    def test_multipe_subscriptions(self):
        frame = Frame(Command.UNSUBSCRIBE,
                      {'id': 'ad052acb-a934-4e10-8ec3-00c7417ef8d1'})

        subscription = TestSubscription('jms.queue.events',
                                        'ad052acb-a934-4e10-8ec3-00c7417ef8d1')
        subscription2 = TestSubscription('jms.queue.events',
                                         'e8a93a6-d886-4cfa-97b9-2d54209053ff')

        destinations = defaultdict(list)
        destinations['jms.queue.events'].append(subscription)
        destinations['jms.queue.events'].append(subscription2)

        adapter = StompAdapterImpl(Reactor(), destinations, {})
        adapter._sub_ids['ad052acb-a934-4e10-8ec3-00c7417ef8d1'] = subscription
        adapter.handle_frame(TestDispatcher(adapter), frame)

        self.assertEqual(len(adapter._sub_ids), 0)
        self.assertEqual(len(destinations), 1)
        self.assertEqual(destinations['jms.queue.events'], [subscription2])

    def test_no_id(self):
        frame = Frame(Command.UNSUBSCRIBE)

        adapter = StompAdapterImpl(Reactor(), defaultdict(list), {})
        adapter.handle_frame(TestDispatcher(adapter), frame)

        resp_frame = adapter.pop_message()
        self.assertEqual(resp_frame.command, Command.ERROR)
        self.assertEqual(resp_frame.body, 'Missing id header')


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
        adapter.handle_frame(TestDispatcher(adapter), frame)

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
        adapter.handle_frame(TestDispatcher(adapter), frame)

        data = adapter.pop_message()
        self.assertIsNot(data, None)
        request = JsonRpcRequest.decode(data)
        self.assertEqual(request.method, 'Host.getAllVmStats')
        self.assertEqual(len(ids), 1)

    def test_send_no_destination(self):
        frame = Frame(Command.SEND,
                      {Headers.DESTINATION: 'jms.topic.unknown'})

        adapter = StompAdapterImpl(Reactor(), defaultdict(list), {})
        adapter.handle_frame(TestDispatcher(adapter), frame)

        resp_frame = adapter.pop_message()
        self.assertEqual(resp_frame.command, Command.ERROR)
        self.assertEqual(resp_frame.body, 'Subscription not available')

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
        adapter.handle_frame(TestDispatcher(adapter), frame)

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

        subscription = TestSubscription('jms.topic.destination',
                                        'e8a936a6-d886-4cfa-97b9-2d54209053ff')

        destinations = defaultdict(list)
        destinations['jms.topic.destination'].append(subscription)

        adapter = StompAdapterImpl(Reactor(), destinations, {})
        subscription.set_client(adapter)
        adapter.handle_frame(TestDispatcher(adapter), frame)

        resp_frame = adapter.pop_message()
        self.assertEqual(resp_frame.command, Command.MESSAGE)
