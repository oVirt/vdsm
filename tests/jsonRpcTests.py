#
# Copyright 2012 Red Hat, Inc.
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
from contextlib import contextmanager

from testrunner import VdsmTestCase as TestCaseBase, \
    expandPermutations, \
    permutations, \
    dummyTextGenerator

from jsonRpcHelper import \
    PERMUTATIONS, \
    constructClient

from yajsonrpc import \
    JsonRpcError, \
    JsonRpcMethodNotFoundError, \
    JsonRpcInternalError, \
    JsonRpcRequest


CALL_TIMEOUT = 5
CALL_ID = '2c8134fd-7dd4-4cfc-b7f8-6b7549399cb6'


class _DummyBridge(object):
    log = logging.getLogger("tests.DummyBridge")

    def getBridgeMethods(self):
        return ((self.echo, 'echo'),
                (self.ping, 'ping'))

    def echo(self, text):
        self.log.info("ECHO: '%s'", text)
        return text

    def ping(self):
        return None


@expandPermutations
class JsonRpcServerTests(TestCaseBase):
    def _callTimeout(self, client, methodName, params=None, rid=None,
                     timeout=None):
        call = client.call_async(JsonRpcRequest(methodName, params, rid))
        self.assertTrue(call.wait(timeout))
        resp = call.responses[0]
        if resp.error is not None:
            raise JsonRpcError(resp.error['code'], resp.error['message'])

        return resp.result

    @contextmanager
    def _client(self, clientFactory):
            client = clientFactory()
            client.setTimeout(CALL_TIMEOUT)
            client.connect()
            try:
                yield client
            finally:
                client.close()

    @permutations(PERMUTATIONS)
    def testMethodCallArgList(self, ssl, type):
        data = dummyTextGenerator(1024)

        bridge = _DummyBridge()
        with constructClient(self.log, bridge, ssl, type) as clientFactory:
            with self._client(clientFactory) as client:
                self.log.info("Calling 'echo'")
                if type == "xml":
                    response = client.send("echo", (data,))
                    self.assertEquals(response, data)
                else:
                    self.assertEquals(self._callTimeout(client, "echo",
                                      (data,), CALL_ID,
                                      CALL_TIMEOUT), data)

    @permutations(PERMUTATIONS)
    def testMethodCallArgDict(self, ssl, type):
        data = dummyTextGenerator(1024)

        bridge = _DummyBridge()
        with constructClient(self.log, bridge, ssl, type) as clientFactory:
            with self._client(clientFactory) as client:
                if type == "xml":
                        response = client.send("echo", (data,))
                        self.assertEquals(response, data)
                else:
                    self.assertEquals(self._callTimeout(client, "echo",
                                      {'text': data}, CALL_ID,
                                      CALL_TIMEOUT), data)

    @permutations(PERMUTATIONS)
    def testMethodMissingMethod(self, ssl, type):
        bridge = _DummyBridge()
        with constructClient(self.log, bridge, ssl, type) as clientFactory:
            with self._client(clientFactory) as client:
                if type == "xml":
                    response = client.send("I.DO.NOT.EXIST :(", ())
                    self.assertTrue("\"I.DO.NOT.EXIST :(\" is not supported"
                                    in response)
                else:
                    with self.assertRaises(JsonRpcError) as cm:
                        self._callTimeout(client, "I.DO.NOT.EXIST :(", [],
                                          CALL_ID, CALL_TIMEOUT)

                    self.assertEquals(cm.exception.code,
                                      JsonRpcMethodNotFoundError().code)

    @permutations(PERMUTATIONS)
    def testMethodBadParameters(self, ssl, type):
        # Without a schema the server returns an internal error

        bridge = _DummyBridge()
        with constructClient(self.log, bridge, ssl, type) as clientFactory:
            with self._client(clientFactory) as client:
                if type == "xml":
                    response = client.send("echo", ())
                    self.assertTrue("echo() takes exactly 2 arguments"
                                    in response)
                else:
                    with self.assertRaises(JsonRpcError) as cm:
                        self._callTimeout(client, "echo", [],
                                          CALL_ID, timeout=CALL_TIMEOUT)

                    self.assertEquals(cm.exception.code,
                                      JsonRpcInternalError().code)

    @permutations(PERMUTATIONS)
    def testMethodReturnsNullAndServerReturnsTrue(self, ssl, type):
        bridge = _DummyBridge()
        with constructClient(self.log, bridge, ssl, type) as clientFactory:
            with self._client(clientFactory) as client:
                if type == "xml":
                    response = client.send("ping", ())
                    # for xml empty response is not allowed by design
                    self.assertTrue("None unless allow_none is enabled"
                                    in response)
                else:
                    res = self._callTimeout(client, "ping", [],
                                            CALL_ID, timeout=CALL_TIMEOUT)
                    self.assertEquals(res, True)
