import json
import socket
import logging
import uuid
from jsonrpc import \
    JsonRpcError, \
    tcpReactor


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

        self._transport.sendMessage(json.dumps(msg, 'utf-8'), timeout=timeout)
        # Notifications have no repsonse
        if reqId is None:
            return

        resp = self._transport.recvMessage(timeout=timeout)
        resp = json.loads(resp)
        if resp.get('error') is not None:
            raise JsonRpcError(resp['error']['code'],
                               resp['error']['message'])

        return resp.get('result')

    def close(self):
        self._transport.close()


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


class ProtonReactorClient(object):
    log = logging.getLogger("ProtonReactorClient")

    def __init__(self, brokerAddress):
        if proton is None:
            raise ImportError("qpid-proton python bindings are not installed")
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
        self.log.debug("Waiting for message")
        try:
            self._msngr.recv(1)
        finally:
            self.log.debug("Done waiting for message")

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
