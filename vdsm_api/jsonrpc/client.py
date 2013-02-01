import json
import socket
import logging
import uuid
from jsonrpc import \
    JsonRpcError, \
    asyncoreReactor

_Size = asyncoreReactor._Size

proton = None
try:
    import proton
    from jsonrpc import protonReactor
    proton         # Squash pyflakes error for
    protonReactor  # unused import
except ImportError:
    pass


class JsonRpcClient(object):
    def __init__(self, reactorClient):
        self._transport = reactorClient

    def connect(self):
        self._transport.connect()

    def callMethod(self, methodName, params=(), reqId=None, timeout=None):
        msg = {'jsonrpc': '2.0',
               'method': methodName,
               'params': params,
               'id': reqId}

        self._transport.send(json.dumps(msg, 'utf-8'), timeout=timeout)
        # Notifications have no repsonse
        if reqId is None:
            return

        resp = self._transport.recv(timeout=timeout)
        resp = json.loads(resp)
        if resp.get('error') is not None:
            raise JsonRpcError(resp['error']['code'],
                               resp['error']['message'])

        return resp.get('result')

    def close(self):
        self._transport.close()


class ProtonReactorClient(object):
    log = logging.getLogger("ProtonReactorClient")

    def __init__(self, brokerAddress):
        self._serverAddress = brokerAddress
        self._msngr = proton.Messenger("client-%s" % str(uuid.uuid4()))

    def connect(self):
        self._msngr.start()

    def send(self, data, timeout=None):
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

    def recv(self, timeout=None):
        if timeout is None:
            timeout = -1
        else:
            timeout *= 1000

        self._msngr.timeout = timeout
        self.log.debug("Waiting for message")
        try:
            self._msngr.recv(1)
        finally:
            self.log.debug("Done waiting for message")

        if not self._msngr.incoming:
            raise socket.timeout()

        msg = proton.Message()
        t = self._msngr.get(msg)
        self._msngr.settle(t)

        return msg.body

    def close(self):
        self._msngr.timeout = 1000
        self._msngr.stop()
