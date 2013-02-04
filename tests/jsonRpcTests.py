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
from Queue import Queue
from contextlib import closing

from testrunner import VdsmTestCase as TestCaseBase, \
    expandPermutations, \
    permutations, \
    dummyTextGenerator

from jsonRpcUtils import \
    REACTOR_TYPE_PERMUTATIONS, \
    constructReactor, \
    constructServer

from yajsonrpc import \
    JsonRpcError, \
    JsonRpcMethodNotFoundError, \
    JsonRpcInternalError


CALL_TIMEOUT = 5


class _EchoMessageHandler(object):
    def handleMessage(self, msgCtx):
        msgCtx.sendReply(msgCtx.data)


class _EchoServer(object):
    log = logging.getLogger("EchoServer")

    def __init__(self):
        self._queue = Queue()

    def accept(self, l, c):
        c.setInbox(self._queue)

    def serve(self):
        while True:
            try:
                client, msg = self._queue.get()
                if client is None:
                    return

                self.log.info("Echoing message")
                client.send(msg)
            except Exception:
                self.log.error("EchoServer died unexpectedly", exc_info=True)


@expandPermutations
class ReactorTests(TestCaseBase):
    @permutations(REACTOR_TYPE_PERMUTATIONS)
    def test(self, reactorType):
        data = dummyTextGenerator(((2 ** 10) * 200))
        queue = Queue()

        echosrv = _EchoServer()

        def serve(reactor):
            try:
                reactor.process_requests()
            except socket.error as e:
                pass
            except Exception as e:
                self.log.error("Reactor died unexpectedly", exc_info=True)
                self.fail("Reactor died: (%s) %s" % (type(e), e))

        with constructReactor(reactorType) as \
                (reactor, clientFactory, laddr):

            t = threading.Thread(target=echosrv.serve)
            t.setDaemon(True)
            t.start()

            reactor.createListener(laddr, echosrv.accept)

            clientNum = 4
            repeats = 2
            subRepeats = 4

            clients = []
            try:
                for i in range(clientNum):
                    c = clientFactory()
                    c.connect()
                    clients.append(c)

                for i in range(repeats):
                    for client in clients:
                        for i in range(subRepeats):
                            self.log.info("Sending message...")
                            client.send(data, CALL_TIMEOUT)

                for i in range(repeats * subRepeats):
                    for client in clients:
                            self.log.info("Waiting for reply...")
                            retData = client.recv(CALL_TIMEOUT)
                            self.log.info("Asserting reply...")
                            self.assertEquals(
                                retData, data,
                                "Data is not as expected " +
                                "'%s...%s' != '%s...%s'" %
                                (retData[:10], retData[-10:],
                                 data[:10], data[-10:]))
            finally:
                queue.put((None, None))


class _DummyBridge(object):
    def echo(self, text):
        return text

    def ping(self):
        return None


@expandPermutations
class JsonRpcServerTests(TestCaseBase):
    @permutations(REACTOR_TYPE_PERMUTATIONS)
    def testMethodCallArgList(self, reactorType):
        data = dummyTextGenerator(1024)

        bridge = _DummyBridge()
        with constructServer(reactorType, bridge) as (server, clientFactory):
            client = clientFactory()
            client.connect()
            with closing(client):
                self.assertEquals(client.callMethod("echo", (data,), 10,
                                                    CALL_TIMEOUT),
                                  data)

    @permutations(REACTOR_TYPE_PERMUTATIONS)
    def testMethodCallArgDict(self, reactorType):
        data = dummyTextGenerator(1024)

        bridge = _DummyBridge()
        with constructServer(reactorType, bridge) as (server, clientFactory):
            client = clientFactory()
            client.connect()
            with closing(client):
                self.assertEquals(client.callMethod("echo",
                                                    {'text': data},
                                                    10, CALL_TIMEOUT),
                                  data)

    @permutations(REACTOR_TYPE_PERMUTATIONS)
    def testMethodMissingMethod(self, reactorType):
        bridge = _DummyBridge()
        with constructServer(reactorType, bridge) as (server, clientFactory):
            client = clientFactory()
            client.connect()
            with closing(client):
                with self.assertRaises(JsonRpcError) as cm:
                    client.callMethod("I.DO.NOT.EXIST :(", [], 10,
                                      CALL_TIMEOUT)

                self.assertEquals(cm.exception.code,
                                  JsonRpcMethodNotFoundError().code)

    @permutations(REACTOR_TYPE_PERMUTATIONS)
    def testMethodBadParameters(self, reactorType):
        # Without a schema the server returns an internal error

        bridge = _DummyBridge()
        with constructServer(reactorType, bridge) as (server, clientFactory):
            client = clientFactory()
            client.connect()
            with closing(client):
                with self.assertRaises(JsonRpcError) as cm:
                    client.callMethod("echo", [], 10, timeout=CALL_TIMEOUT)

                self.assertEquals(cm.exception.code,
                                  JsonRpcInternalError().code)

    @permutations(REACTOR_TYPE_PERMUTATIONS)
    def testMethodReturnsNull(self, reactorType):
        bridge = _DummyBridge()
        with constructServer(reactorType, bridge) as (server, clientFactory):
            client = clientFactory()
            client.connect()
            with closing(client):
                res = client.callMethod("ping", [], 10, timeout=CALL_TIMEOUT)

                self.assertEquals(res, None)
