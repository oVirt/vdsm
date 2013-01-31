import threading
import socket
from Queue import Queue
from contextlib import closing
from contextlib import contextmanager
from functools import partial
from nose.plugins.skip import SkipTest

from jsonrpc import \
    JsonRpcServer, \
    asyncoreReactor
from jsonrpc.client import \
    JsonRpcClient, \
    TCPReactorClient, \
    ProtonReactorClient

protonReactor = None
try:
    import proton
    from jsonrpc import protonReactor
    proton         # Squash pyflakes error for
    protonReactor  # unused import
except ImportError:
    pass


def getFreePort():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    with closing(sock):
        try:
            sock.bind(("0.0.0.0", 0))
        except:
            raise Exception("Could not find a free port")
        return sock.getsockname()[1]


@contextmanager
def _tcpServerConstructor():
    port = getFreePort()
    address = ("localhost", port)
    reactor = asyncoreReactor.AsyncoreReactor()

    try:
        yield reactor, partial(TCPReactorClient, address), address
    finally:
        reactor.stop()


@contextmanager
def _protonServerConstructor():
    if protonReactor is None:
        raise SkipTest("qpid-proton python bindings are not installed")

    port = getFreePort()
    serverAddress = "amqp://127.0.0.1:%d/vdsm_test" % (port,)
    reactor = protonReactor.ProtonReactor()

    try:
        yield (reactor,
               partial(ProtonReactorClient, serverAddress),
               ("127.0.0.1", port))
    finally:
        reactor.stop()


REACTOR_CONSTRUCTORS = {"tcp": _tcpServerConstructor,
                        "proton": _protonServerConstructor}
REACTOR_TYPE_PERMUTATIONS = [[r] for r in REACTOR_CONSTRUCTORS.iterkeys()]


@contextmanager
def constructReactor(tp):
    with REACTOR_CONSTRUCTORS[tp]() as res:
        yield res


@contextmanager
def constructServer(tp, bridge):
    queue = Queue()
    server = JsonRpcServer(bridge, queue)
    with constructReactor(tp) as (reactor, clientFactory, laddr):
        t = threading.Thread(target=reactor.process_requests)
        t.setDaemon(True)
        t.start()

        def _accept(listener, client):
            client.setInbox(queue)

        reactor.createListener(laddr, _accept)

        t = threading.Thread(target=server.serve_requests)
        t.setDaemon(True)
        t.start()

        def jsonClientFactory():
            return JsonRpcClient(clientFactory())

        try:
            yield server, jsonClientFactory
        finally:
            server.stop()
