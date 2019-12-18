#
# Copyright 2012-2017 Red Hat, Inc.
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

from __future__ import absolute_import
from __future__ import division
import logging
import time
from contextlib import contextmanager
from six.moves import queue
from monkeypatch import MonkeyPatch
from testValidation import slowtest
from vdsm import executor
from vdsm.common import exception

from testlib import VdsmTestCase as TestCaseBase, \
    expandPermutations, \
    permutations, \
    dummyTextGenerator

from testValidation import broken_on_ci

from integration.sslhelper import generate_key_cert_pair, create_ssl_context

from integration.jsonRpcHelper import \
    constructClient

from yajsonrpc import JsonRpcRequest
from yajsonrpc.exception import \
    JsonRpcErrorBase, \
    JsonRpcMethodNotFoundError, \
    JsonRpcNoResponseError, \
    JsonRpcInternalError


CALL_TIMEOUT = 3
EVENT_TIMEOUT = 5
CALL_ID = '2c8134fd-7dd4-4cfc-b7f8-6b7549399cb6'
EVENT_TOPIC = "jms.topic.test"
USE_SSL = [[True], [False]]


class _DummyBridge(object):
    log = logging.getLogger("tests.DummyBridge")
    cif = None

    def getBridgeMethods(self):
        return ((self.echo, 'echo'),
                (self.ping, 'ping'),
                (self.slow_response, 'slow_response'))

    def dispatch(self, method):
        try:
            return getattr(self, method)
        except AttributeError:
            raise JsonRpcMethodNotFoundError(method=method)

    def echo(self, text):
        self.log.info("ECHO: '%s'", text)
        return text

    @property
    def event_schema(self):
        return FakeSchema()

    def ping(self):
        return None

    def slow_response(self):
        time.sleep(CALL_TIMEOUT + 2)

    def send_event(self):
        self.cif.notify('|vdsm|test_event|', {'content': True}, EVENT_TOPIC)
        return 'sent'

    def register_server_address(self, server_address):
        self.server_address = server_address

    def unregister_server_address(self):
        self.server_address = None


class FakeSchema(object):

    def verify_event_params(self, event_id, kwargs):
        pass


def dispatch(callable, timeout=None):
    raise exception.ResourceExhausted(resource="test", current_tasks=0)


@expandPermutations
class JsonRpcServerTests(TestCaseBase):

    def run(self, result=None):
        with generate_key_cert_pair() as key_cert_pair:
            key_file, cert_file = key_cert_pair
            self.ssl_ctx = create_ssl_context(key_file, cert_file)
            super(TestCaseBase, self).run(result)

    def _callTimeout(self, client, methodName, params=None, rid=None,
                     timeout=None):
        responses = client.call(JsonRpcRequest(methodName, params, rid),
                                timeout=CALL_TIMEOUT)
        if not responses:
            raise JsonRpcNoResponseError(method=methodName)
        resp = responses[0]
        if resp.error is not None:
            raise resp.error

        return resp.result

    @contextmanager
    def _client(self, clientFactory):
            client = clientFactory()
            try:
                yield client
            finally:
                client.close()

    def _get_with_timeout(self, event_queue):
        try:
            return event_queue.get(timeout=EVENT_TIMEOUT)
        except queue.Empty:
            self.fail("Event queue timed out.")

    def _collect_events(self, event_queue):
        res = []

        while True:
            ev = self._get_with_timeout(event_queue)
            if ev is None:
                break

            res.append(ev)

        return res

    @permutations(USE_SSL)
    @broken_on_ci(reason="Fails randomly in CI")
    def testMethodCallArgList(self, use_ssl):
        data = dummyTextGenerator(1024)
        ssl_ctx = self.ssl_ctx if use_ssl else None
        bridge = _DummyBridge()

        with constructClient(self.log, bridge, ssl_ctx) as clientFactory:
            with self._client(clientFactory) as client:
                self.log.info("Calling 'echo'")
                self.assertEqual(self._callTimeout(client, "echo",
                                                   (data,), CALL_ID), data)

    @permutations(USE_SSL)
    @broken_on_ci("fails randomly in CI")
    def testMethodCallArgDict(self, use_ssl):
        data = dummyTextGenerator(1024)
        ssl_ctx = self.ssl_ctx if use_ssl else None
        bridge = _DummyBridge()

        with constructClient(self.log, bridge, ssl_ctx) as clientFactory:
            with self._client(clientFactory) as client:
                self.assertEqual(self._callTimeout(client, "echo",
                                 {'text': data}, CALL_ID), data)

    @permutations(USE_SSL)
    @broken_on_ci("fails randomly in CI")
    def testMethodMissingMethod(self, use_ssl):
        missing_method = "I_DO_NOT_EXIST :("
        ssl_ctx = self.ssl_ctx if use_ssl else None
        bridge = _DummyBridge()

        with constructClient(self.log, bridge, ssl_ctx) as clientFactory:
            with self._client(clientFactory) as client:
                with self.assertRaises(JsonRpcErrorBase) as cm:
                    self._callTimeout(client, missing_method, [],
                                      CALL_ID)

                self.assertEqual(
                    cm.exception.code,
                    JsonRpcMethodNotFoundError(method=missing_method).code)
                self.assertIn(missing_method, cm.exception.msg)

    @permutations(USE_SSL)
    @broken_on_ci("fails randomly in CI")
    def testMethodBadParameters(self, use_ssl):
        # Without a schema the server returns an internal error
        ssl_ctx = self.ssl_ctx if use_ssl else None
        bridge = _DummyBridge()

        with constructClient(self.log, bridge, ssl_ctx) as clientFactory:
            with self._client(clientFactory) as client:
                with self.assertRaises(JsonRpcErrorBase) as cm:
                    self._callTimeout(client, "echo", [],
                                      CALL_ID)

                self.assertEqual(cm.exception.code,
                                 JsonRpcInternalError().code)

    @permutations(USE_SSL)
    @broken_on_ci("fails randomly in CI")
    def testMethodReturnsNullAndServerReturnsTrue(self, use_ssl):
        ssl_ctx = self.ssl_ctx if use_ssl else None
        bridge = _DummyBridge()

        with constructClient(self.log, bridge, ssl_ctx) as clientFactory:
            with self._client(clientFactory) as client:
                res = self._callTimeout(client, "ping", [],
                                        CALL_ID)
                self.assertEqual(res, True)

    @slowtest
    @permutations(USE_SSL)
    def testSlowMethod(self, use_ssl):
        ssl_ctx = self.ssl_ctx if use_ssl else None
        bridge = _DummyBridge()

        with constructClient(self.log, bridge, ssl_ctx) as clientFactory:
            with self._client(clientFactory) as client:
                with self.assertRaises(JsonRpcErrorBase) as cm:
                    self._callTimeout(client, "slow_response", [], CALL_ID)

                self.assertEqual(cm.exception.code,
                                 JsonRpcNoResponseError().code)

    @MonkeyPatch(executor.Executor, 'dispatch', dispatch)
    @permutations(USE_SSL)
    @broken_on_ci("fails randomly in CI")
    def testFullExecutor(self, use_ssl):
        ssl_ctx = self.ssl_ctx if use_ssl else None
        bridge = _DummyBridge()

        with constructClient(self.log, bridge, ssl_ctx) as clientFactory:
            with self._client(clientFactory) as client:
                with self.assertRaises(JsonRpcErrorBase) as cm:
                    self._callTimeout(client, "no_method", [], CALL_ID)

                self.assertEqual(cm.exception.code,
                                 JsonRpcInternalError().code)

    @permutations(USE_SSL)
    @broken_on_ci("fails randomly in CI")
    def testClientSubscribe(self, use_ssl):
        ssl_ctx = self.ssl_ctx if use_ssl else None
        bridge = _DummyBridge()

        with constructClient(self.log, bridge, ssl_ctx) as clientFactory:
            with self._client(clientFactory) as client:
                event_queue = queue.Queue()
                sub = client.subscribe(EVENT_TOPIC, event_queue)

                res = self._callTimeout(client, "send_event", [],
                                        CALL_ID)
                self.assertEqual(res, 'sent')
                client.unsubscribe(sub)

                events = self._collect_events(event_queue)
                self.assertEqual(len(events), 1)

                event, event_params = events[0]
                self.assertEqual(event, '|vdsm|test_event|')
                self.assertEqual(event_params['content'], True)

    @permutations(USE_SSL)
    @broken_on_ci("fails randomly in CI")
    def testClientNotify(self, use_ssl):
        ssl_ctx = self.ssl_ctx if use_ssl else None
        bridge = _DummyBridge()

        with constructClient(self.log, bridge, ssl_ctx) as clientFactory:
            with self._client(clientFactory) as client:
                event_queue = queue.Queue()
                custom_topic = 'custom.topic'
                sub = client.subscribe(custom_topic, event_queue)

                client.notify('vdsm.event', custom_topic,
                              bridge.event_schema, {'content': True})

                # Waiting for event before unsubscribing, to make sure,
                # it will be received
                event, event_params = self._get_with_timeout(event_queue)
                self.assertEqual(event, 'vdsm.event')
                self.assertEqual(event_params['content'], True)

                client.unsubscribe(sub)
                events = self._collect_events(event_queue)
                self.assertEqual(len(events), 0)
