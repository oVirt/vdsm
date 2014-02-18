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
import threading
import socket
import logging
import apiTests
from Queue import Queue
from contextlib import contextmanager
from testValidation import brokentest

from testrunner import VdsmTestCase as TestCaseBase, \
    expandPermutations, \
    permutations, \
    dummyTextGenerator

from jsonRpcUtils import \
    CONNECTION_PERMUTATIONS, \
    constructReactor, \
    constructServer

from yajsonrpc import \
    JsonRpcError, \
    JsonRpcMethodNotFoundError, \
    JsonRpcInternalError, \
    JsonRpcRequest


CALL_TIMEOUT = 5


class _EchoMessageHandler(object):
    def handleMessage(self, msgCtx):
        msgCtx.sendReply(msgCtx.data)


class _EchoServer(object):
    log = logging.getLogger("EchoServer")

    def __init__(self):
        self._queue = Queue()

    def accept(self, l, c):
        c.setMessageHandler(self._queue.put_nowait)

    def serve(self):
        while True:
            try:
                client, msg = self._queue.get()
                if client is None:
                    return

                client.send(msg)
            except Exception:
                self.log.error("EchoServer died unexpectedly", exc_info=True)


class ReactorClientSyncWrapper(object):
    def __init__(self, client):
        self._client = client
        self._queue = Queue()
        self._client.setMessageHandler(self._queue.put_nowait)

    def send(self, data):
        self._client.send(data)

    def connect(self):
        self._client.setTimeout(CALL_TIMEOUT)
        self._client.connect()

    def recv(self, timeout=None):
        return self._queue.get(True, timeout)[1]


@expandPermutations
class ReactorTests(TestCaseBase):
    @brokentest
    @permutations(CONNECTION_PERMUTATIONS)
    def test(self, rt, ssl):
        data = dummyTextGenerator(((2 ** 10) * 200))

        echosrv = _EchoServer()

        def serve(reactor):
            try:
                reactor.process_requests()
            except socket.error as e:
                pass
            except Exception as e:
                self.log.error("Reactor died unexpectedly", exc_info=True)
                self.fail("Reactor died: (%s) %s" % (type(e), e))

        with constructReactor(rt) as \
                (reactor, clientReactor, laddr):

            t = threading.Thread(target=echosrv.serve)
            t.setDaemon(True)
            t.start()

            reactor.createListener(laddr, echosrv.accept)

            clientNum = 2
            repeats = 10
            subRepeats = 10

            clients = []
            for i in range(clientNum):
                c = ReactorClientSyncWrapper(
                    clientReactor.createClient(laddr))
                c.connect()
                clients.append(c)

            for i in range(repeats):
                for client in clients:
                    for i in range(subRepeats):
                        self.log.info("Sending message...")
                        client.send(data)

            for i in range(repeats * subRepeats):
                for client in clients:
                    self.log.info("Waiting for reply...")
                    retData = client.recv(CALL_TIMEOUT)
                    self.log.info("Asserting reply...")
                    self.assertTrue(isinstance(retData,
                                               (str, unicode)))
                    plen = 20  # Preview len, used for debugging
                    self.assertEquals(
                        retData, data,
                        "Data is not as expected " +
                        "'%s...%s' (%d chars) != '%s...%s' (%d chars)" %
                        (retData[:plen], retData[-plen:], len(retData),
                         data[:plen], data[-plen:], len(data)))


class _DummyBridge(object):
    def echo(self, text):
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

    @permutations(CONNECTION_PERMUTATIONS)
    def testMethodCallArgList(self, rt, ssl):
        data = dummyTextGenerator(1024)

        bridge = _DummyBridge()
        with constructServer(rt, bridge, ssl) as (server, clientFactory):
            with self._client(clientFactory) as client:
                self.assertEquals(self._callTimeout(client, "echo", (data,),
                                  apiTests.id,
                                  CALL_TIMEOUT), data)

    @permutations(CONNECTION_PERMUTATIONS)
    def testMethodCallArgDict(self, rt, ssl):
        data = dummyTextGenerator(1024)

        bridge = _DummyBridge()
        with constructServer(rt, bridge, ssl) as (server, clientFactory):
            with self._client(clientFactory) as client:
                self.assertEquals(self._callTimeout(client, "echo",
                                  {'text': data},
                                  apiTests.id,
                                  CALL_TIMEOUT), data)

    @permutations(CONNECTION_PERMUTATIONS)
    def testMethodMissingMethod(self, rt, ssl):
        bridge = _DummyBridge()
        with constructServer(rt, bridge, ssl) as (server, clientFactory):
            with self._client(clientFactory) as client:
                with self.assertRaises(JsonRpcError) as cm:
                    self._callTimeout(client, "I.DO.NOT.EXIST :(", [],
                                      apiTests.id,
                                      CALL_TIMEOUT)

                self.assertEquals(cm.exception.code,
                                  JsonRpcMethodNotFoundError().code)

    @permutations(CONNECTION_PERMUTATIONS)
    def testMethodBadParameters(self, rt, ssl):
        # Without a schema the server returns an internal error

        bridge = _DummyBridge()
        with constructServer(rt, bridge, ssl) as (server, clientFactory):
            with self._client(clientFactory) as client:
                with self.assertRaises(JsonRpcError) as cm:
                    self._callTimeout(client, "echo", [],
                                      apiTests.id,
                                      timeout=CALL_TIMEOUT)

                self.assertEquals(cm.exception.code,
                                  JsonRpcInternalError().code)

    @permutations(CONNECTION_PERMUTATIONS)
    def testMethodReturnsNullAndServerReturnsTrue(self, rt, ssl):
        bridge = _DummyBridge()
        with constructServer(rt, bridge, ssl) as (server, clientFactory):
            with self._client(clientFactory) as client:
                res = self._callTimeout(client, "ping", [],
                                        apiTests.id,
                                        timeout=CALL_TIMEOUT)
                self.assertEquals(res, True)
