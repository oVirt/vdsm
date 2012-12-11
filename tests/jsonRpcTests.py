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
from contextlib import contextmanager
from functools import partial
from contextlib import closing
import json
import uuid

from nose.plugins.skip import SkipTest

from testrunner import VdsmTestCase as TestCaseBase, \
    expandPermutations, \
    permutations, \
    dummyTextGenerator

from jsonrpc import \
    tcpReactor, \
    JsonRpcError, \
    JsonRpcServer, \
    JsonRpcMethodNotFoundError, \
    JsonRpcInternalError

protonReactor = None
try:
    import proton
    from jsonrpc import protonReactor
except ImportError:
    pass
PORT_RANGE = xrange(49152, 65535)


_distributedPorts = []


def _getFreePort():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    with closing(sock):
        for port in PORT_RANGE:
            if port in _distributedPorts:
                continue

            try:
                sock.bind(("0.0.0.0", port))
            except:
                continue

            _distributedPorts.append(port)
            return port
        else:
            raise Exception("Could not find a free port")


@contextmanager
def _tcpServerConstructor(messageHandler):
    port = _getFreePort()
    address = ("localhost", port)
    reactor = tcpReactor.TCPReactor(address, messageHandler)

    try:
        yield reactor, partial(TCPReactorClient, address)
    finally:
        reactor.stop()


@contextmanager
def _protonServerConstructor(messageHandler):
    if protonReactor is None:
        raise SkipTest("qpid-proton python bindings are not installed")

    port = _getFreePort()
    serverAddress = "amqp://127.0.0.1:%d/vdsm_test" % (port,)
    reactor = protonReactor.ProtonReactor(("127.0.0.1", port), messageHandler)

    try:
        yield reactor, partial(ProtonReactorClient, serverAddress)
    finally:
        reactor.stop()


REACTOR_CONSTRUCTORS = {"tcp": _tcpServerConstructor,
                        "proton": _protonServerConstructor}
REACTOR_TYPE_PERMUTATIONS = [[r] for r in REACTOR_CONSTRUCTORS.iterkeys()]


@contextmanager
def constructReactor(tp, messageHandler):
    with REACTOR_CONSTRUCTORS[tp](messageHandler) as res:
        yield res


@contextmanager
def constructServer(tp, bridge):
    server = JsonRpcServer(bridge)
    with constructReactor(tp, server) as (reactor, clientFactory):
        reactor.start_listening()
        t = threading.Thread(target=reactor.process_requests)
        t.setDaemon(True)
        t.start()

        def jsonClientFactory():
            return JsonRpcClient(clientFactory())

        yield server, jsonClientFactory


class _EchoMessageHandler(object):
    def handleMessage(self, msgCtx):
        msgCtx.sendReply(msgCtx.data)


class TCPReactorClient(object):
    def __init__(self, address):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.address = address

    def connect(self):
        self.sock.connect(self.address)

    def sendMessage(self, msg, timeout=None):
        msg = tcpReactor._Size.pack(len(msg)) + msg
        self.sock.settimeout(timeout)
        while msg:
            sent = self.sock.send(msg)
            msg = msg[sent:]

    def recvMessage(self, timeout=None):
        self.sock.settimeout(timeout)
        rawSize = self.sock.recv(tcpReactor._Size.size)
        size = tcpReactor._Size.unpack(rawSize)[0]
        buff = ""
        while (size - len(buff)) > 0:
            buff += self.sock.recv(size)

        return buff

    def close(self):
        self.sock.close()


class JsonRpcClient(object):
    def __init__(self, reactorClient):
        self._transport = reactorClient

    def connect(self):
        self._transport.connect()

    def callMethod(self, methodName, params=(), reqId=None):
        msg = {'jsonrpc': '2.0',
               'method': methodName,
               'params': params,
               'id': reqId}

        self._transport.sendMessage(json.dumps(msg, 'utf-8'))
        # Notifications have no repsonse
        if reqId is None:
            return

        resp = self._transport.recvMessage()
        resp = json.loads(resp)
        if resp.get('error') is not None:
            raise JsonRpcError(resp['error']['code'],
                               resp['error']['message'])

        return resp.get('result')

    def close(self):
        self._transport.close()


class ProtonReactorClient(object):
    def __init__(self, brokerAddress):
        self._serverAddress = brokerAddress
        self._msngr = proton.Messenger("client-%s" % str(uuid.uuid4()))

    def connect(self):
        self._msngr.start()

    def sendMessage(self, data, timeout=None):
        if timeout is None:
            timeout = -1
        else:
            timeout *= 1000

        msg = proton.Message()
        msg.address = self._serverAddress
        msg.body = unicode(data)
        self._msngr.timeout = timeout
        t = self._msngr.put(msg)
        try:
            self._msngr.send()
        except:
            self._msngr.settle(t)
            raise

    def recvMessage(self, timeout=None):
        if timeout is None:
            timeout = -1
        else:
            timeout *= 1000

        self._msngr.timeout = timeout
        self._msngr.recv(1)

        if not self._msngr.incoming:
            raise socket.timeout()

        if self._msngr.incoming > 1:
            raise Exception("Got %d repsones instead of 1" %
                            self._msngr.incoming)

        msg = proton.Message()
        t = self._msngr.get(msg)
        self._msngr.settle(t)
        return msg.body

    def close(self):
        self._msngr.timeout = 1000
        self._msngr.stop()


@expandPermutations
class ReactorTests(TestCaseBase):
    @permutations(REACTOR_TYPE_PERMUTATIONS)
    def test(self, reactorType):
        data = dummyTextGenerator(((2 ** 10) * 200))
        msgHandler = _EchoMessageHandler()

        def serve(reactor):
            try:
                reactor.process_requests()
            except socket.error as e:
                pass
            except Exception as e:
                self.log.error("Server died unexpectedly", exc_info=True)
                self.fail("Server died: (%s) %s" % (type(e), e))

        with constructReactor(reactorType, msgHandler) as (reactor,
                                                           clientFactory):
            reactor.start_listening()
            t = threading.Thread(target=serve, args=(reactor,))
            t.setDaemon(True)
            t.start()

            clientNum = 1
            repeats = 1
            subRepeats = 1

            clients = []
            try:
                for i in range(clientNum):
                    client = clientFactory()
                    client.connect()
                    clients.append(client)

                for i in range(repeats):
                    for client in clients:
                        for i in range(subRepeats):
                            self.log.info("Sending message...")
                            client.sendMessage(data, 1)

                for i in range(repeats * subRepeats):
                    for client in clients:
                            self.log.info("Waiting for reply...")
                            retData = client.recvMessage(1)
                            self.log.info("Asserting reply...")
                            self.assertEquals(retData, data)
            finally:
                for client in clients:
                    client.close()


class _DummyBridge(object):
    def echo(self, text):
        return text


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
                self.assertEquals(client.callMethod("echo", (data,), 10), data)

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
                                                    10),
                                  data)

    @permutations(REACTOR_TYPE_PERMUTATIONS)
    def testMethodMissingMethod(self, reactorType):
        bridge = _DummyBridge()
        with constructServer(reactorType, bridge) as (server, clientFactory):
            client = clientFactory()
            client.connect()
            with closing(client):
                with self.assertRaises(JsonRpcError) as cm:
                    client.callMethod("I.DO.NOT.EXIST :(", [], 10)

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
                    client.callMethod("echo", [], 10)

                self.assertEquals(cm.exception.code,
                                  JsonRpcInternalError().code)
