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
import os
import threading
import logging

import stomp

from betterAsyncore import Dispatcher
from vdsm.sslutils import SSLSocket

_STATE_LEN = "Waiting for message length"
_STATE_MSG = "Waiting for message"


_DEFAULT_RESPONSE_DESTINATIOM = "/queue/_local/vdsm/reponses"
_DEFAULT_REQUEST_DESTINATION = "/queue/_local/vdsm/requests"


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
        self._reactor.wakeup()

    def _cmd_subscribe(self, dispatcher, frame):
        self.log.debug("Subscribe command ignored")

    def _cmd_unsubscribe(self, dispatcher, frame):
        self.log.debug("Unsubscribe command ignored")

    def _cmd_send(self, dispatcher, frame):
        self._messageHandler(self, frame.body)

    def handle_frame(self, dispatcher, frame):
        self.log.debug("Handling message %s", frame)
        try:
            self._commands[frame.command](dispatcher, frame)
        except KeyError:
            self.log.warn("Unknown command %s", frame)
            dispatcher.handle_error()


class _StompConnection(object):
    def __init__(self, aclient, sock, reactor):
        self._socket = sock
        self._reactor = reactor
        self._messageHandler = None

        adisp = self._adisp = stomp.AsyncDispatcher(aclient)
        self._dispatcher = Dispatcher(adisp, sock=sock, map=reactor._map)

    def send_raw(self, msg):
        self._adisp.send_raw(msg)
        self._reactor.wakeup()

    def setTimeout(self, timeout):
        self._dispatcher.socket.settimeout(timeout)

    def connect(self):
        pass

    def close(self):
        self._dispatcher.close()


class StompServer(object):
    log = logging.getLogger("yajsonrpc.StompServer")

    def __init__(self, sock, reactor):
        self._reactor = reactor
        self._messageHandler = None
        self._socket = sock

        adapter = StompAdapterImpl(reactor, self._handleMessage)
        self._stompConn = _StompConnection(
            adapter,
            sock,
            reactor,
        )

    def setTimeout(self, timeout):
        self._stompConn.setTimeout(timeout)

    def connect(self):
        self._stompConn.connect()

    def _handleMessage(self, impl, data):
        if self._messageHandler is not None:
            self._messageHandler((self, data))

    def setMessageHandler(self, msgHandler):
        self._messageHandler = msgHandler
        self.check_read()

    def check_read(self):
        if isinstance(self._socket, SSLSocket) and self._socket.pending() > 0:
            self._stompConn._dispatcher.handle_read()

    def send(self, message):
        self.log.debug("Sending response")
        res = stomp.Frame(stomp.Command.MESSAGE,
                          {"destination": _DEFAULT_RESPONSE_DESTINATIOM,
                           "content-type": "application/json"},
                          message)
        self._stompConn.send_raw(res)

    def close(self):
        self._stompConn.close()

    def get_local_address(self):
        return self._socket.getsockname()[0]


class StompClient(object):
    log = logging.getLogger("jsonrpc.AsyncoreClient")

    def __init__(self, sock, reactor):
        self._reactor = reactor
        self._messageHandler = None
        self._socket = sock

        self._aclient = stomp.AsyncClient(self, "vdsm")
        self._stompConn = _StompConnection(
            self._aclient,
            sock,
            reactor
        )

    def setTimeout(self, timeout):
        self._stompConn.setTimeout(timeout)

    def connect(self):
        self._stompConn.connect()

    def handle_message(self, impl, frame):
        if self._messageHandler is not None:
            self._messageHandler((self, frame.body))

    def setMessageHandler(self, msgHandler):
        self._messageHandler = msgHandler
        self.check_read()

    def check_read(self):
        if isinstance(self._socket, SSLSocket) and self._socket.pending() > 0:
            self._stompConn._dispatcher.handle_read()

    def send(self, message):
        self.log.debug("Sending response")
        self._aclient.send(self._stompConn, _DEFAULT_REQUEST_DESTINATION,
                           message,
                           {"content-type": "application/json"})

    def close(self):
        self._stompConn.close()


def StompListener(reactor, acceptHandler, connected_socket):
    impl = StompListenerImpl(reactor, acceptHandler, connected_socket)
    return Dispatcher(impl, connected_socket, map=reactor._map)


# FIXME: We should go about making a listener wrapper like the client wrapper
#        This is not as high priority as users don't interact with listeners
#        as much
class StompListenerImpl(object):
    log = logging.getLogger("jsonrpc.StompListener")

    def __init__(self, reactor, acceptHandler, connected_socket):
        self._reactor = reactor
        self._socket = connected_socket
        self._acceptHandler = acceptHandler

    def init(self, dispatcher):
        dispatcher.set_reuse_addr()

        client = StompServer(self._socket, self._reactor)
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
    def __init__(self):
        self._map = {}
        self._isRunning = False
        self._wakeupEvent = _AsyncoreEvent(self._map)

    def createListener(self, connected_socket, acceptHandler):
        listener = StompListener(self, acceptHandler, connected_socket)
        self.wakeup()
        return listener

    def createClient(self, connected_socket):
        return StompClient(connected_socket, self)

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


class StompDetector():
    log = logging.getLogger("protocoldetector.StompDetector")
    NAME = "stomp"
    REQUIRED_SIZE = max(len(s) for s in stomp.COMMANDS)

    def __init__(self, json_binding):
        self.json_binding = json_binding
        self._reactor = self.json_binding.createStompReactor()

    def detect(self, data):
        return data.startswith(stomp.COMMANDS)

    def handleSocket(self, client_socket, socket_address):
        self.json_binding.add_socket(self._reactor, client_socket)
        self.log.debug("Stomp detected from %s", socket_address)
