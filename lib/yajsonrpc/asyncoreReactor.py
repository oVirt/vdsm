# Copyright (C) 2014 Saggi Mizrahi, Red Hat Inc.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 2 as
# published by the Free Software Foundation.
#
# This program is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public
# License along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin St, Fifth Floor, Boston, MA 02110-1301 USA

import struct
import asyncore
import socket
import os
import threading
import logging

from betterAsyncore import \
    AsyncChat, \
    Dispatcher, \
    SSLDispatcher, \
    SSLContext


_Size = struct.Struct("!Q")

_STATE_LEN = "Waiting for message length"
_STATE_MSG = "Waiting for message"


def _SSLContextFactory(ctxdef):
    """Creates an appropriate ssl context from the generic defenition defined
    in __init__.py"""
    return SSLContext(cert_file=ctxdef.cert_file,
                      key_file=ctxdef.key_file,
                      ca_cert=ctxdef.ca_cert,
                      session_id=ctxdef.session_id,
                      protocol=ctxdef.protocol)


class AsyncoreClientImpl(object):
    log = logging.getLogger("jsonrpc.AsyncoreClient")

    def __init__(self, messageHandler):
        self._ibuff = []
        self._mlen = _Size.size
        self._state = _STATE_LEN
        self._sending = threading.Lock()
        self._messageHandler = messageHandler

    def init(self, achat):
        achat.set_terminator(_Size.size)

    def collect_incoming_data(self, data, achat):
        self._ibuff.append(data)

    def found_terminator(self, achat):
        ibuff = ''.join(self._ibuff)

        l = self._mlen
        if len(ibuff) > l:
            self._ibuff = [ibuff[l:]]
            ibuff = ibuff[:l]
        else:
            self._ibuff = []

        if self._state == _STATE_LEN:
            mlen = _Size.unpack(ibuff)[0]
            self.log.debug("Got request to recv %d bytes", mlen)
            self._state = _STATE_MSG
            achat.set_terminator(mlen)
            self._mlen = mlen

        elif self._state == _STATE_MSG:
            self._messageHandler(self, ibuff)

            achat.set_terminator(_Size.size)
            self._mlen = _Size.size
            self._state = _STATE_LEN

    def buildMessage(self, data):
        return _Size.pack(len(data)) + data


class AsyncoreClient(object):
    log = logging.getLogger("jsonrpc.AsyncoreClient")

    def __init__(self, sock, reactor, addr, sslctx=None):
        self._addr = addr
        self._reactor = reactor
        self._messageHandler = None

        self._impl = impl = AsyncoreClientImpl(self._handleMessage)
        self._chat = chat = AsyncChat(impl)
        if sslctx is None:
            try:
                sslctx = sock.get_context()
            except AttributeError:
                # if get_context() is missing it just means we recieved an
                # socket that doesn't use SSL
                pass
        else:
            sslctx = _SSLContextFactory(sslctx)

        if sslctx is None:
            dispatcher = Dispatcher(chat, sock=sock, map=reactor._map)
        else:
            dispatcher = SSLDispatcher(chat, sslctx, sock=sock,
                                       map=reactor._map)

        if sock is None:
            address_family = socket.getaddrinfo(*addr)[0][0]
            dispatcher.create_socket(address_family, socket.SOCK_STREAM)

        self._dispatcher = dispatcher

    def setTimeout(self, timeout):
        self._dispatcher.socket.settimeout(timeout)

    def connect(self):
        self._dispatcher.connect(self._addr)

    def _handleMessage(self, impl, data):
        if self._messageHandler is not None:
            self.log.debug("Queueing incoming message")
            self._messageHandler((self, data))

    def setMessageHandler(self, msgHandler):
        self._messageHandler = msgHandler

    def send(self, message):
        self._chat.push(self._impl.buildMessage(message), self._dispatcher)
        self._reactor.wakeup()

    def close(self):
        self._dispatcher.close()


def AsyncoreListener(reactor, address, acceptHandler, sslctx=None):
    impl = AsyncoreListenerImpl(reactor, address, acceptHandler)
    if sslctx is None:
        return Dispatcher(impl, map=reactor._map)
    else:
        sslctx = _SSLContextFactory(sslctx)
        return SSLDispatcher(impl, sslctx, map=reactor._map)


# FIXME: We should go about making a listener wrapper like the client wrapper
#        This is not as high priority as users don't interact with listeners
#        as much
class AsyncoreListenerImpl(object):
    log = logging.getLogger("jsonrpc.AsyncoreListener")

    def __init__(self, reactor, address, acceptHandler):
        self._reactor = reactor
        self._address = address
        self._acceptHandler = acceptHandler

    def init(self, dispatcher):
        address_family = socket.getaddrinfo(*self._address)[0][0]
        dispatcher.create_socket(address_family, socket.SOCK_STREAM)

        dispatcher.set_reuse_addr()
        dispatcher.bind(self._address)
        dispatcher.listen(5)

    def handle_accept(self, dispatcher):
        try:
            pair = dispatcher.accept()
        except Exception as e:
            self.log.exception(e)
            raise
        if pair is None:
            return

        sock, addr = pair
        self.log.debug("Accepting connection from client "
                       "at tcp://%s:%s", addr[0], addr[1])

        client = AsyncoreClient(sock, self._reactor, addr)
        self._acceptHandler(self, client)

    def writable(self, dispatcher):
        return False


class _AsyncoreEvent(asyncore.file_dispatcher):
    def __init__(self, map):
        self._lock = threading.Lock()
        r, w = os.pipe()
        self._w = w
        try:
            asyncore.file_dispatcher.__init__(self, r, map=map)
        except:
            os.close(r)
            os.close(w)
            raise

        # the file_dispatcher ctor dups the file in order to take ownership of
        # it
        os.close(r)
        self._isSet = False

    def writable(self):
        return False

    def set(self):
        with self._lock:
            if self._isSet:
                return

            self._isSet = True

        os.write(self._w, "a")

    def handle_read(self):
        with self._lock:
            self.recv(1)
            self._isSet = False

    def close(self):
        try:
            os.close(self._w)
        except (OSError, IOError):
            pass

        asyncore.file_dispatcher.close(self)


class AsyncoreReactor(object):
    def __init__(self, sslctx=None):
        self.sslctx = sslctx
        self._map = {}
        self._isRunning = False
        self._wakeupEvent = _AsyncoreEvent(self._map)

    def createListener(self, address, acceptHandler):
        listener = AsyncoreListener(self, address, acceptHandler, self.sslctx)
        self.wakeup()
        return listener

    def createClient(self, address):
        return AsyncoreClient(None, self, address, self.sslctx)

    def process_requests(self):
        self._isRunning = True
        while self._isRunning:
            asyncore.loop(use_poll=True, map=self._map, count=1)

        for key, dispatcher in self._map.items():
            del self._map[key]
            dispatcher.close()

    def wakeup(self):
        self._wakeupEvent.set()

    def stop(self):
        self._isRunning = False
        try:
            self.wakeup()
        except (IOError, OSError):
            # Client woke up and closed the event dispatcher without our help
            pass
