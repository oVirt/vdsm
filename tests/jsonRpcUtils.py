import threading
import socket
from contextlib import closing
from contextlib import contextmanager
from functools import partial
from nose.plugins.skip import SkipTest

from jsonrpc import \
    JsonRpcServer, \
    tcpReactor
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


_PORT_RANGE = xrange(49152, 65535)


_distributedPorts = []


def getFreePort():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    with closing(sock):
        for port in _PORT_RANGE:
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
    port = getFreePort()
    address = ("localhost", port)
    reactor = tcpReactor.TCPReactor(messageHandler)

    try:
        yield reactor, partial(TCPReactorClient, address), address
    finally:
        reactor.stop()


@contextmanager
def _protonServerConstructor(messageHandler):
    if protonReactor is None:
        raise SkipTest("qpid-proton python bindings are not installed")

    port = getFreePort()
    serverAddress = "amqp://127.0.0.1:%d/vdsm_test" % (port,)
    reactor = protonReactor.ProtonReactor(messageHandler)

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
def constructReactor(tp, messageHandler):
    with REACTOR_CONSTRUCTORS[tp](messageHandler) as res:
        yield res


@contextmanager
def constructServer(tp, bridge):
    server = JsonRpcServer(bridge)
    with constructReactor(tp, server) as (reactor, clientFactory, laddr):
        t = threading.Thread(target=reactor.process_requests)
        t.setDaemon(True)
        t.start()
        reactor.start_listening(laddr)

        t = threading.Thread(target=server.serve_requests)
        t.setDaemon(True)
        t.start()

        def jsonClientFactory():
            return JsonRpcClient(clientFactory())

        try:
            yield server, jsonClientFactory
        finally:
            server.stop()
