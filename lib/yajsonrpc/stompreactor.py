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

import logging
from collections import deque

import stomp

from betterAsyncore import Dispatcher, Reactor
from vdsm.sslutils import SSLSocket
from yajsonrpc import JsonRpcClient

_STATE_LEN = "Waiting for message length"
_STATE_MSG = "Waiting for message"


_DEFAULT_RESPONSE_DESTINATIOM = "/queue/_local/vdsm/reponses"
_DEFAULT_REQUEST_DESTINATION = "/queue/_local/vdsm/requests"

_FAKE_SUB_ID = "__vdsm_fake_broker__"


def parseHeartBeatHeader(v):
    try:
        x, y = v.split(",", 1)
    except ValueError:
        x, y = (0, 0)

    try:
        x = int(x)
    except ValueError:
        x = 0

    try:
        y = int(y)
    except ValueError:
        y = 0

    return (x, y)


class StompAdapterImpl(object):
    log = logging.getLogger("Broker.StompAdapter")

    def __init__(self, reactor, messageHandler):
        self._reactor = reactor
        self._outbox = deque()
        self._messageHandler = messageHandler
        self._commands = {
            stomp.Command.CONNECT: self._cmd_connect,
            stomp.Command.SEND: self._cmd_send,
            stomp.Command.SUBSCRIBE: self._cmd_subscribe,
            stomp.Command.UNSUBSCRIBE: self._cmd_unsubscribe}

    @property
    def has_outgoing_messages(self):
        return (len(self._outbox) > 0)

    def peek_message(self):
        return self._outbox[0]

    def pop_message(self):
        return self._outbox.popleft()

    def queue_frame(self, frame):
        self._outbox.append(frame)

    def _cmd_connect(self, dispatcher, frame):
        self.log.info("Processing CONNECT request")
        version = frame.headers.get(stomp.Headers.ACCEPT_VERSION, None)
        if version != "1.2":
            resp = stomp.Frame(
                stomp.Command.ERROR,
                None,
                "Version unsupported"
            )
        else:
            resp = stomp.Frame(stomp.Command.CONNECTED, {"version": "1.2"})
            cx, cy = parseHeartBeatHeader(
                frame.headers.get(stomp.Headers.HEARTEBEAT, "0,0")
            )

            # Make sure the heart-beat interval is sane
            if cy != 0:
                cy = max(cy, 1000)

            # The server can send a heart-beat every cy ms and doesn't want
            # to receive any heart-beat from the client.
            resp.headers[stomp.Headers.HEARTEBEAT] = "%d,0" % (cy,)
            dispatcher.setHeartBeat(cy)

        self.queue_frame(resp)
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

        self._async_client = aclient
        adisp = self._adisp = stomp.AsyncDispatcher(aclient)
        self._dispatcher = Dispatcher(adisp, sock=sock, map=reactor._map)

    def send_raw(self, msg):
        self._async_client.queue_frame(msg)
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

    def set_message_handler(self, msgHandler):
        self._messageHandler = msgHandler
        self.check_read()

    def check_read(self):
        self._stompConn._dispatcher.handle_read_event()

    def send(self, message):
        self.log.debug("Sending response")
        res = stomp.Frame(
            stomp.Command.MESSAGE,
            {
                stomp.Headers.DESTINATION: _DEFAULT_RESPONSE_DESTINATIOM,
                stomp.Headers.SUBSCRIPTION: _FAKE_SUB_ID,
                stomp.Headers.CONTENT_TYPE: "application/json",
            },
            message
        )
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

        self._aclient = stomp.AsyncClient()
        self._stompConn = _StompConnection(
            self._aclient,
            sock,
            reactor
        )
        self._aclient.handle_connect()

    def setTimeout(self, timeout):
        self._stompConn.setTimeout(timeout)

    def connect(self):
        self._stompConn.connect()

    def handle_message(self, sub, frame):
        if self._messageHandler is not None:
            self._messageHandler((self, frame.body))

    def set_message_handler(self, msgHandler):
        self._messageHandler = msgHandler
        self.check_read()

    def check_read(self):
        if isinstance(self._socket, SSLSocket) and self._socket.pending() > 0:
            self._stompConn._dispatcher.handle_read()

    def subscribe(self, *args, **kwargs):
        return self._aclient.subscribe(*args, **kwargs)

    def send(self, message, destination=_DEFAULT_REQUEST_DESTINATION,
             headers=None):
        self.log.debug("Sending response")
        self._aclient.send(
            destination,
            message,
            headers
        )
        self._reactor.wakeup()

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


class StompReactor(object):
    def __init__(self):
        self._reactor = Reactor()

    def createListener(self, connected_socket, acceptHandler):
        listener = StompListener(
            self._reactor,
            acceptHandler,
            connected_socket
        )
        self._reactor.wakeup()
        return listener

    def createClient(self, connected_socket):
        return StompClient(connected_socket, self._reactor)

    def process_requests(self):
        self._reactor.process_requests()

    def stop(self):
        self._reactor.stop()


class StompDetector():
    log = logging.getLogger("protocoldetector.StompDetector")
    NAME = "stomp"
    REQUIRED_SIZE = max(len(s) for s in stomp.COMMANDS)

    def __init__(self, json_binding):
        self.json_binding = json_binding
        self._reactor = self.json_binding.createStompReactor()

    def detect(self, data):
        return data.startswith(stomp.COMMANDS)

    def handle_socket(self, client_socket, socket_address):
        self.json_binding.add_socket(self._reactor, client_socket)
        self.log.debug("Stomp detected from %s", socket_address)


class ClientRpcTransportAdapter(object):
    def __init__(self, sub, destination, client):
        self._sub = sub
        sub.set_message_handler(self._handle_message)
        self._destination = destination
        self._client = client
        self._message_handler = lambda arg: None

    """
    In order to process message we need to set message
    handler which is responsible for processing jsonrpc
    content of the message. Currently there are 2 handlers:
    JsonRpcClient and JsonRpcServer.
    """
    def set_message_handler(self, handler):
        self._message_handler = handler

    def send(self, data):
        headers = {
            "content-type": "application/json",
            "reply-to": self._sub.destination,
        }
        self._client.send(
            data,
            self._destination,
            headers,
        )

    def _handle_message(self, sub, frame):
        self._message_handler((self, frame.body))

    def close(self):
        self._sub.unsubscribe()


def StompRpcClient(stomp_client, request_queue, response_queue):
    sub_id = _FAKE_SUB_ID if request_queue == _FAKE_SUB_ID else None

    return JsonRpcClient(
        ClientRpcTransportAdapter(
            stomp_client.subscribe(response_queue, sub_id=sub_id),
            request_queue,
            stomp_client,
        )
    )
