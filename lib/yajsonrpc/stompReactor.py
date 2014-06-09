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

import asyncore
import socket
import os
import threading
import logging

import stomp

from betterAsyncore import \
    Dispatcher, \
    SSLDispatcher
from vdsm.sslutils import SSLContext


_STATE_LEN = "Waiting for message length"
_STATE_MSG = "Waiting for message"


_DEFAULT_RESPONSE_DESTINATIOM = "/queue/_local/vdsm/reponses"
_DEFAULT_REQUEST_DESTINATION = "/queue/_local/vdsm/requests"


def _SSLContextFactory(ctxdef):
    """Creates an appropriate ssl context from the generic defenition defined
    in __init__.py"""
    return SSLContext(cert_file=ctxdef.cert_file,
                      key_file=ctxdef.key_file,
                      ca_cert=ctxdef.ca_cert,
                      session_id=ctxdef.session_id,
                      protocol=ctxdef.protocol)


class StompAdapterImpl(object):
    log = logging.getLogger("Broker.StompAdapter")

    def __init__(self, reactor, messageHandler):
        self._reactor = reactor
        self._messageHandler = messageHandler
        self._commands = {
            stomp.Command.CONNECT: self._cmd_connect,
            stomp.Command.SEND: self._cmd_send,
            stomp.Command.SUBSCRIBE: self._cmd_subscribe,
            stomp.Command.UNSUBSCRIBE: self._cmd_unsubscribe}

    def _cmd_connect(self, dispatcher, frame):
        self.log.info("Processing CONNECT request")
        version = frame.headers.get("accept-version", None)
        if version != "1.2":
            res = stomp.Frame(stomp.Command.ERROR, None, "Version unsupported")

        else:
            res = stomp.Frame(stomp.Command.CONNECTED, {"version": "1.2"})

        dispatcher.send_raw(res)
        self.log.info("CONNECT response queued")
        self._reactor.wakeup()

    def _cmd_subscribe(self, dispatcher, frame):
        self.log.debug("Subscribe command ignored")

    def _cmd_unsubscribe(self, dispatcher, frame):
        self.log.debug("Unsubscribe command ignored")

    def _cmd_send(self, dispatcher, frame):
        self.log.debug("Passing incoming message")
        self._messageHandler(self, frame.body)

    def handle_frame(self, dispatcher, frame):
        self.log.debug("Handling message %s", frame)
        try:
            self._commands[frame.command](dispatcher, frame)
        except KeyError:
            self.log.warn("Unknown command %s", frame)
            dispatcher.handle_error()


class _StompConnection(object):
    def __init__(self, aclient, sock, reactor, addr, sslctx=None):
        self._addr = addr
        self._reactor = reactor
        self._messageHandler = None

        adisp = self._adisp = stomp.AsyncDispatcher(aclient)
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
            dispatcher = Dispatcher(adisp, sock=sock, map=reactor._map)
        else:
            dispatcher = SSLDispatcher(adisp, sslctx, sock=sock,
                                       map=reactor._map)

        if sock is None:
            address_family = socket.getaddrinfo(*addr)[0][0]
            dispatcher.create_socket(address_family, socket.SOCK_STREAM)

        self._dispatcher = dispatcher

    def send_raw(self, msg):
        self._adisp.send_raw(msg)
        self._reactor.wakeup()

    def setTimeout(self, timeout):
        self._dispatcher.socket.settimeout(timeout)

    def connect(self):
        self._dispatcher.connect(self._addr)

    def close(self):
        self._dispatcher.close()


class StompServer(object):
    log = logging.getLogger("yajsonrpc.StompServer")

    def __init__(self, sock, reactor, addr, sslctx=None):
        self._addr = addr
        self._reactor = reactor
        self._messageHandler = None

        adapter = StompAdapterImpl(reactor, self._handleMessage)
        self._stompConn = _StompConnection(
            adapter,
            sock,
            reactor,
            addr,
            sslctx
        )

    def setTimeout(self, timeout):
        self._stompConn.setTimeout(timeout)

    def connect(self):
        self._stompConn.connect()

    def _handleMessage(self, impl, data):
        if self._messageHandler is not None:
            self.log.debug("Processing incoming message")
            self._messageHandler((self, data))

    def setMessageHandler(self, msgHandler):
        self._messageHandler = msgHandler

    def send(self, message):
        self.log.debug("Sending response")
        res = stomp.Frame(stomp.Command.MESSAGE,
                          {"destination": _DEFAULT_RESPONSE_DESTINATIOM,
                           "content-type": "application/json"},
                          message)
        self._stompConn.send_raw(res)

    def close(self):
        self._stompConn.close()


class StompClient(object):
    log = logging.getLogger("jsonrpc.AsyncoreClient")

    def __init__(self, sock, reactor, addr, sslctx=None):
        self._addr = addr
        self._reactor = reactor
        self._messageHandler = None

        self._aclient = stomp.AsyncClient(self, "vdsm")
        self._stompConn = _StompConnection(
            self._aclient,
            sock,
            reactor,
            addr,
            sslctx
        )

    def setTimeout(self, timeout):
        self._stompConn.setTimeout(timeout)

    def connect(self):
        self._stompConn.connect()

    def handle_message(self, impl, frame):
        if self._messageHandler is not None:
            self.log.debug("Queueing incoming message")
            self._messageHandler((self, frame.body))

    def setMessageHandler(self, msgHandler):
        self._messageHandler = msgHandler

    def send(self, message):
        self._aclient.send(self._stompConn, _DEFAULT_REQUEST_DESTINATION,
                           message,
                           {"content-type": "application/json"})
        self.log.debug("Message queued for delivery")

    def close(self):
        self._stompConn.close()


def StompListener(reactor, address, acceptHandler, sslctx=None):
    impl = StompListenerImpl(reactor, address, acceptHandler)
    if sslctx is None:
        return Dispatcher(impl, map=reactor._map)
    else:
        sslctx = _SSLContextFactory(sslctx)
        return SSLDispatcher(impl, sslctx, map=reactor._map)


# FIXME: We should go about making a listener wrapper like the client wrapper
#        This is not as high priority as users don't interact with listeners
#        as much
class StompListenerImpl(object):
    log = logging.getLogger("jsonrpc.StompListener")

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

        client = StompServer(sock, self._reactor, addr)
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


class StompReactor(object):
    def __init__(self, sslctx=None):
        self.sslctx = sslctx
        self._map = {}
        self._isRunning = False
        self._wakeupEvent = _AsyncoreEvent(self._map)

    def createListener(self, address, acceptHandler):
        listener = StompListener(self, address, acceptHandler, self.sslctx)
        self.wakeup()
        return listener

    def createClient(self, address):
        return StompClient(None, self, address, self.sslctx)

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
