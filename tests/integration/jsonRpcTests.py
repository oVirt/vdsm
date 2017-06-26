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
import logging
import time
from contextlib import contextmanager
from monkeypatch import MonkeyPatch
from testValidation import slowtest
from vdsm import executor

from testlib import VdsmTestCase as TestCaseBase, \
    expandPermutations, \
    permutations, \
    dummyTextGenerator

from jsonRpcHelper import \
    PERMUTATIONS, \
    constructClient

from yajsonrpc import \
    JsonRpcErrorBase, \
    JsonRpcMethodNotFoundError, \
    JsonRpcNoResponseError, \
    JsonRpcInternalError, \
    JsonRpcRequest


CALL_TIMEOUT = 3
CALL_ID = '2c8134fd-7dd4-4cfc-b7f8-6b7549399cb6'


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

    def double_response(self):
        self.cif.notify('vdsm.double_response', {'content': True})
        return 'sent'

    def register_server_address(self, server_address):
        self.server_address = server_address

    def unregister_server_address(self):
        self.server_address = None


class FakeSchema(object):

    def verify_event_params(self, event_id, kwargs):
        pass


def dispatch(callable, timeout=None):
    raise executor.TooManyTasks


@expandPermutations
class JsonRpcServerTests(TestCaseBase):
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

    @permutations(PERMUTATIONS)
    def testMethodCallArgList(self, ssl):
        data = dummyTextGenerator(1024)

        bridge = _DummyBridge()
        with constructClient(self.log, bridge, ssl) as clientFactory:
            with self._client(clientFactory) as client:
                self.log.info("Calling 'echo'")
                self.assertEqual(self._callTimeout(client, "echo",
                                                   (data,), CALL_ID), data)

    @permutations(PERMUTATIONS)
    def testMethodCallArgDict(self, ssl):
        data = dummyTextGenerator(1024)

        bridge = _DummyBridge()
        with constructClient(self.log, bridge, ssl) as clientFactory:
            with self._client(clientFactory) as client:
                self.assertEqual(self._callTimeout(client, "echo",
                                 {'text': data}, CALL_ID), data)

    @permutations(PERMUTATIONS)
    def testMethodMissingMethod(self, ssl):
        missing_method = "I_DO_NOT_EXIST :("

        bridge = _DummyBridge()
        with constructClient(self.log, bridge, ssl) as clientFactory:
            with self._client(clientFactory) as client:
                with self.assertRaises(JsonRpcErrorBase) as cm:
                    self._callTimeout(client, missing_method, [],
                                      CALL_ID)

                self.assertEqual(
                    cm.exception.code,
                    JsonRpcMethodNotFoundError(method=missing_method).code)
                self.assertIn(missing_method, cm.exception.message)

    @permutations(PERMUTATIONS)
    def testMethodBadParameters(self, ssl):
        # Without a schema the server returns an internal error

        bridge = _DummyBridge()
        with constructClient(self.log, bridge, ssl) as clientFactory:
            with self._client(clientFactory) as client:
                with self.assertRaises(JsonRpcErrorBase) as cm:
                    self._callTimeout(client, "echo", [],
                                      CALL_ID)

                self.assertEqual(cm.exception.code,
                                 JsonRpcInternalError().code)

    @permutations(PERMUTATIONS)
    def testMethodReturnsNullAndServerReturnsTrue(self, ssl):
        bridge = _DummyBridge()
        with constructClient(self.log, bridge, ssl) as clientFactory:
            with self._client(clientFactory) as client:
                res = self._callTimeout(client, "ping", [],
                                        CALL_ID)
                self.assertEqual(res, True)

    @permutations(PERMUTATIONS)
    def testDoubleResponse(self, ssl):
        bridge = _DummyBridge()
        with constructClient(self.log, bridge, ssl) as clientFactory:
            with self._client(clientFactory) as client:
                def callback(client, event, params):
                    self.assertEqual(event, 'vdsm.double_response')
                    self.assertEqual(params['content'], True)

                client.registerEventCallback(callback)
                res = self._callTimeout(client, "double_response", [],
                                        CALL_ID)
                self.assertEqual(res, 'sent')

    @slowtest
    @permutations(PERMUTATIONS)
    def testSlowMethod(self, ssl):
        bridge = _DummyBridge()
        with constructClient(self.log, bridge, ssl) as clientFactory:
            with self._client(clientFactory) as client:
                with self.assertRaises(JsonRpcErrorBase) as cm:
                    self._callTimeout(client, "slow_response", [], CALL_ID)

                self.assertEqual(cm.exception.code,
                                 JsonRpcNoResponseError().code)

    @MonkeyPatch(executor.Executor, 'dispatch', dispatch)
    @permutations(PERMUTATIONS)
    def testFullExecutor(self, ssl):
        bridge = _DummyBridge()
        with constructClient(self.log, bridge, ssl) as clientFactory:
            with self._client(clientFactory) as client:
                with self.assertRaises(JsonRpcErrorBase) as cm:
                    self._callTimeout(client, "no_method", [], CALL_ID)

                self.assertEqual(cm.exception.code,
                                 JsonRpcInternalError().code)

    @permutations(PERMUTATIONS)
    def testClientSubscribe(self, ssl):
        bridge = _DummyBridge()
        with constructClient(self.log, bridge, ssl) as clientFactory:
            with self._client(clientFactory) as client:
                def callback(client, event, params):
                    self.assertEqual(event, 'vdsm.double_response')
                    self.assertEqual(params['content'], True)

                sub = client.subscribe("jms.topic.test")
                client.registerEventCallback(callback)
                res = self._callTimeout(client, "double_response", [],
                                        CALL_ID)
                self.assertEqual(res, 'sent')
                client.unsubscribe(sub)

    @permutations(PERMUTATIONS)
    def testClientNotify(self, ssl):
        bridge = _DummyBridge()
        with constructClient(self.log, bridge, ssl) as clientFactory:
            with self._client(clientFactory) as client:
                def callback(client, event, params):
                    self.assertEqual(event, 'vdsm.event')
                    self.assertEqual(params['content'], True)

                client.registerEventCallback(callback)
                client.notify('vdsm.event', 'jms.topic.test',
                              bridge.event_schema, {'content': True})
