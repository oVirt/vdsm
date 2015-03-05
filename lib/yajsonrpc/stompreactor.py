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
from collections import deque, defaultdict
from uuid import uuid4
import functools

import stomp
from vdsm import utils
from vdsm.compat import json
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

    """
    This class is responsible for stomp message processing
    in the server side. It uses two dictionaries to track
    request/response destinations.

    sub_map - maps a destination id to _Subsctiption object
              representing stomp subscription.
    req_dest - maps a request id to a destination.
    """
    def __init__(self, reactor, sub_map, req_dest):
        self._reactor = reactor
        self._outbox = deque()
        self._sub_dests = sub_map
        self._req_dest = req_dest
        self._sub_ids = {}
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
        self.log.info("Subscribe command received")
        destination = frame.headers.get("destination", None)
        sub_id = frame.headers.get("id", None)

        if not destination or not sub_id:
            self._send_error("Missing destination or subscription id header",
                             dispatcher.connection)
            return

        ack = frame.headers.get("ack", stomp.AckMode.AUTO)
        subscription = stomp._Subscription(dispatcher.connection, destination,
                                           sub_id, ack, None)

        self._sub_dests[destination].append(subscription)
        self._sub_ids[sub_id] = subscription

    def _send_error(self, msg, connection):
        res = stomp.Frame(
            stomp.Command.ERROR,
            None,
            msg
        )
        connection.send_raw(res)

    def _cmd_unsubscribe(self, dispatcher, frame):
        self.log.info("Unsubscribe command received")
        sub_id = frame.headers.get("id", None)

        if not sub_id:
            self._send_error("Missing id header",
                             dispatcher.connection)
            return

        try:
            subscription = self._sub_ids.pop(sub_id)
        except KeyError:
            self.log.debug("No subscription for %s id",
                           sub_id)
            return
        subs = self._sub_dests[subscription.destination]
        if len(subs) == 1:
            del self._sub_dests[subscription.destination]
        else:
            if subscription in subs:
                subs.remove(subscription)

    def _cmd_send(self, dispatcher, frame):
        destination = frame.headers.get(stomp.Headers.DESTINATION, None)
        if _DEFAULT_REQUEST_DESTINATION == destination:
            # default subscription
            self._handle_internal(dispatcher,
                                  frame.headers.get(stomp.Headers.REPLY_TO),
                                  frame.body)
            return
        elif stomp.LEGACY_SUBSCRIPTION_ID_REQUEST == destination:
            self._handle_internal(dispatcher,
                                  stomp.LEGACY_SUBSCRIPTION_ID_RESPONSE,
                                  frame.body)
            return
        else:
            try:
                subs = self._sub_dests[destination]
            except KeyError:
                self._send_error("Subscription not available",
                                 dispatcher.connection)
                return

            for subscription in subs:
                headers = utils.picklecopy(frame.headers)
                headers = {stomp.Headers.SUBSCRIPTION: subscription.id}
                headers.update(frame.headers)
                res = stomp.Frame(
                    stomp.Command.MESSAGE,
                    headers,
                    frame.body
                )
                subscription.client.send_raw(res)

    def _handle_internal(self, dispatcher, req_dest, request):
        """
        We need to build response dictionary which maps message id
        with destination. For legacy mode we use known 3.5 destination
        or for standard mode we use 'reply-to' header.
        """
        try:
            self._handle_destination(dispatcher, req_dest, json.loads(request))
        except Exception:
            # let json server process issue
            pass
        dispatcher.connection.handleMessage(request)

    def _handle_destination(self, dispatcher, req_dest, request):
        """
        We could receive single message or batch of messages. We need
        to build response map for each message.
        """
        if isinstance(request, list):
            map(functools.partial(self._handle_destination, dispatcher,
                                  req_dest),
                request)
            return

        self._req_dest[request.get("id")] = req_dest

    def handle_frame(self, dispatcher, frame):
        self.log.debug("Handling message %s", frame)
        try:
            self._commands[frame.command](dispatcher, frame)
        except KeyError:
            self.log.warn("Unknown command %s", frame)
            dispatcher.handle_error()


class _StompConnection(object):

    def __init__(self, server, aclient, sock, reactor):
        self._socket = sock
        self._reactor = reactor
        self._server = server
        self._messageHandler = None

        self._async_client = aclient
        adisp = self._adisp = stomp.AsyncDispatcher(self, aclient)
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

    def get_local_address(self):
        return self._socket.getsockname()[0]

    def set_message_handler(self, msgHandler):
        self._messageHandler = msgHandler
        self._dispatcher.handle_read_event()

    def handleMessage(self, data):
        if self._messageHandler is not None:
            self._messageHandler((self._server, self, data))


class StompServer(object):
    log = logging.getLogger("yajsonrpc.StompServer")

    def __init__(self, reactor):
        self._reactor = reactor
        self._messageHandler = None
        self._sub_map = defaultdict(list)
        self._req_dest = {}

    def add_client(self, sock):
        adapter = StompAdapterImpl(self._reactor, self._sub_map,
                                   self._req_dest)
        return _StompConnection(self, adapter, sock,
                                self._reactor)

    def send(self, message):
        self.log.debug("Sending response")
        destination = _DEFAULT_RESPONSE_DESTINATIOM
        try:
            resp = json.loads(message)
            destination = self._req_dest[resp.get("id")]
            del self._req_dest["id"]
        except KeyError:
            # we could have no reply-to
            pass

        try:
            connections = self._sub_map[destination]
        except KeyError:
            self.log.warn("Attempt to reply to unknown destination %s",
                          destination)
            return

        for connection in connections:
            res = stomp.Frame(
                stomp.Command.MESSAGE,
                {
                    stomp.Headers.DESTINATION: destination,
                    stomp.Headers.CONTENT_TYPE: "application/json",
                    stomp.Headers.SUBSCRIPTION: connection.id
                },
                message
            )
            connection.client.send_raw(res)

    def close(self):
        for connection in self._sub_map.values():
            connection.close()


class StompClient(object):
    log = logging.getLogger("jsonrpc.AsyncoreClient")

    def __init__(self, sock, reactor):
        self._reactor = reactor
        self._messageHandler = None
        self._socket = sock

        self._aclient = stomp.AsyncClient()
        self._stompConn = _StompConnection(
            self,
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
        sub = self._aclient.subscribe(*args, **kwargs)
        self._reactor.wakeup()
        return sub

    def send(self, message, destination=stomp.LEGACY_SUBSCRIPTION_ID_RESPONSE,
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


def StompListener(reactor, server, acceptHandler, connected_socket):
    impl = StompListenerImpl(server, acceptHandler, connected_socket)
    return Dispatcher(impl, connected_socket, map=reactor._map)


# FIXME: We should go about making a listener wrapper like the client wrapper
#        This is not as high priority as users don't interact with listeners
#        as much
class StompListenerImpl(object):
    log = logging.getLogger("jsonrpc.StompListener")

    def __init__(self, server, acceptHandler, connected_socket):
        self._socket = connected_socket
        self._acceptHandler = acceptHandler
        self._server = server

    def init(self, dispatcher):
        dispatcher.set_reuse_addr()

        conn = self._server.add_client(self._socket)
        self._acceptHandler(conn)

    def writable(self, dispatcher):
        return False


class StompReactor(object):
    def __init__(self):
        self._reactor = Reactor()
        self._server = StompServer(self._reactor)

    def createListener(self, connected_socket, acceptHandler):
        listener = StompListener(
            self._reactor,
            self._server,
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
    return JsonRpcClient(
        ClientRpcTransportAdapter(
            stomp_client.subscribe(response_queue, sub_id=str(uuid4())),
            request_queue,
            stomp_client,
        )
    )
