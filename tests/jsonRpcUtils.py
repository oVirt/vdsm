import threading
import socket
from contextlib import closing
from contextlib import contextmanager
from functools import partial
from nose.plugins.skip import SkipTest
from itertools import product
import os

from yajsonrpc import \
    JsonRpcServer, \
    asyncoreReactor, \
    stompReactor, \
    JsonRpcClientPool, \
    SSLContext

protonReactor = None
try:
    from yajsonrpc import protonReactor
    protonReactor  # unused import
except ImportError:
    pass


def hasProton():
    return protonReactor is not None


def getFreePort():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    with closing(sock):
        try:
            sock.bind(("0.0.0.0", 0))
        except:
            raise Exception("Could not find a free port")
        return sock.getsockname()[1]


@contextmanager
def _stompServerConstructor():
    port = getFreePort()
    address = ("127.0.0.1", port)
    reactorType = stompReactor.StompReactor
    yield reactorType, address


@contextmanager
def _tcpServerConstructor():
    port = getFreePort()
    address = ("127.0.0.1", port)
    reactorType = asyncoreReactor.AsyncoreReactor
    yield reactorType, address


@contextmanager
def _protonServerConstructor():
    if protonReactor is None:
        raise SkipTest("qpid-proton python bindings are not installed")

    port = getFreePort()
    reactorType = protonReactor.ProtonReactor

    yield (reactorType,
           ("127.0.0.1", port))


REACTOR_CONSTRUCTORS = {"tcp": _tcpServerConstructor,
                        "amqp": _protonServerConstructor,
                        "stomp": _stompServerConstructor}
REACTOR_TYPE_PERMUTATIONS = [[r] for r in REACTOR_CONSTRUCTORS.iterkeys()]
CONNECTION_PERMUTATIONS = tuple(product(REACTOR_CONSTRUCTORS.iterkeys(),
                                        (True, False)))

CERT_DIR = os.path.abspath(os.path.dirname(__file__))
CRT_FILE = os.path.join(CERT_DIR, "jsonrpc-tests.server.crt")
KEY_FILE = os.path.join(CERT_DIR, "jsonrpc-tests.server.key")
KS_FILE = os.path.join(CERT_DIR, "jsonrpc-tests.p12")

DEAFAULT_SSL_CONTEXT = SSLContext(
    CRT_FILE, KEY_FILE, session_id="json-rpc-tests")


@contextmanager
def constructReactor(tp, ssl=False):
    with REACTOR_CONSTRUCTORS[tp]() as (reactorType, laddr):
        sslctx = DEAFAULT_SSL_CONTEXT if ssl else None

        serverReactor = reactorType(sslctx)
        t = threading.Thread(target=serverReactor.process_requests)
        t.setDaemon(True)
        t.start()

        clientReactor = reactorType(sslctx)
        t = threading.Thread(target=clientReactor.process_requests)
        t.setDaemon(True)
        t.start()

        try:
            yield serverReactor, clientReactor, laddr
        finally:
            clientReactor.stop()
            serverReactor.stop()


@contextmanager
def attachedReactor(tp, server, ssl=False):
    with constructReactor(tp, ssl) as (reactor, clientReactor, laddr):
        def _accept(listener, client):
            client.setMessageHandler(server.queueRequest)

        reactor.createListener(laddr, _accept)

        t = threading.Thread(target=server.serve_requests)
        t.setDaemon(True)
        t.start()

        cpool = JsonRpcClientPool({tp: clientReactor})
        t = threading.Thread(target=cpool.serve)
        t.setDaemon(True)
        t.start()

        curl = "%s://%s:%d" % (tp, laddr[0], laddr[1])
        clientFactory = partial(cpool.createClient, curl)
        clientFactory.listeningAddress = laddr
        yield clientFactory


@contextmanager
def constructServer(tp, bridge, ssl=False):
    server = JsonRpcServer(bridge)
    with attachedReactor(tp, server, ssl) as clientFactory:

        try:
            yield server, clientFactory
        finally:
            server.stop()
