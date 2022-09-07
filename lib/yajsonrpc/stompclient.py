# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

import logging
import six
from collections import deque
from threading import Event
from uuid import uuid4

from vdsm import utils
from vdsm.common import concurrent
from vdsm.common import pki
from vdsm.sslutils import SSLSocket, SSLContext
from yajsonrpc.stomp import \
    AckMode, \
    Command, \
    Frame, \
    Headers, \
    StompConnection, \
    StompError, \
    Subscription, \
    DEFAULT_INCOMING, \
    DEFAULT_OUTGOING, \
    GRACE_PERIOD_FACTOR, \
    NR_RETRIES, \
    RECONNECT_INTERVAL, \
    SUBSCRIPTION_ID_RESPONSE
from yajsonrpc.jsonrpcclient import JsonRpcClient
from yajsonrpc import CALL_TIMEOUT
from .betterAsyncore import Reactor


class AsyncClient(object):
    log = logging.getLogger("yajsonrpc.protocols.stomp.AsyncClient")

    def __init__(self, incoming_heartbeat=DEFAULT_INCOMING,
                 outgoing_heartbeat=DEFAULT_OUTGOING, nr_retries=NR_RETRIES,
                 reconnect_interval=RECONNECT_INTERVAL):
        self._connected = Event()
        self._incoming_heartbeat = incoming_heartbeat
        self._outgoing_heartbeat = outgoing_heartbeat
        self._nr_retries = nr_retries
        self._reconnect_interval = reconnect_interval
        self._outbox = deque()
        self._requests = deque()
        self._error = None
        self._subscriptions = {}
        self._commands = {
            Command.CONNECTED: self._process_connected,
            Command.MESSAGE: self._process_message,
            Command.RECEIPT: self._process_receipt,
            Command.ERROR: self._process_error,
            Command.DISCONNECT: self._process_disconnect
        }

    @property
    def connected(self):
        return self._connected.is_set()

    def queue_frame(self, frame):
        self._outbox.append(frame)

    def queue_resend(self, frame):
        self._requests.append(frame)

    @property
    def has_outgoing_messages(self):
        return (len(self._outbox) > 0)

    @property
    def nr_retries(self):
        return self._nr_retries

    @property
    def reconnect_interval(self):
        return self._reconnect_interval

    def peek_message(self):
        return self._outbox[0]

    def pop_message(self):
        return self._outbox.popleft()

    def getLastError(self):
        return self._error

    def handle_connect(self):
        self._outbox.clear()
        outgoing_heartbeat = \
            int(self._outgoing_heartbeat * (1 + GRACE_PERIOD_FACTOR))
        incoming_heartbeat = \
            int(self._incoming_heartbeat * (1 - GRACE_PERIOD_FACTOR))

        self._outbox.appendleft(Frame(
            Command.CONNECT,
            {
                Headers.ACCEPT_VERSION: "1.2",
                Headers.HEARTBEAT: "%d,%d" % (outgoing_heartbeat,
                                              incoming_heartbeat),
            }
        ))
        self.restore_subscriptions()

    def handle_error(self, dispatcher):
        dispatcher.handle_timeout()

    def handle_close(self, dispatcher):
        dispatcher.handle_timeout()

    def handle_frame(self, dispatcher, frame):
        self._commands[frame.command](frame, dispatcher)

    def handle_timeout(self, dispatcher):
        self.log.debug("Timeout occurred, trying to reconnect")
        dispatcher.connection.reconnect(dispatcher._count,
                                        dispatcher._on_timeout)

    def _process_connected(self, frame, dispatcher):
        self._connected.set()
        dispatcher.connection.set_heartbeat(
            self._outgoing_heartbeat,
            self._incoming_heartbeat)

        self.log.debug("Stomp connection established")

        i = 0
        while i < len(self._requests):
            self._outbox.append(self._requests.popleft())
            i += 1

    def _process_message(self, frame, dispatcher):
        sub_id = frame.headers.get(Headers.SUBSCRIPTION)
        if sub_id is None:
            self.log.warning("Got message without subscription id")
            return
        sub = self._subscriptions.get(sub_id)
        if sub is None:
            self.log.warning(
                "Got message without an unknown subscription id '%s'",
                sub_id
            )
            return

        sub.handle_message(frame)

    def _process_receipt(self, frame, dispatcher):
        self.log.debug("Receipt frame received")

    def _process_error(self, frame, dispatcher):
        raise StompError(frame, frame.body)

    def resend(self, destination, data="", headers=None):
        self.queue_resend(self._build_frame(destination, data, headers))

    def send(self, destination, data="", headers=None):
        frame = self._build_frame(destination, data, headers)
        if not self._connected.wait(timeout=CALL_TIMEOUT):
            self.queue_resend(frame)

        self.queue_frame(frame)

    def _build_frame(self, destination, data="", headers=None):
        final_headers = {"destination": destination}
        if headers is not None:
            final_headers.update(headers)
        return Frame(Command.SEND, final_headers, data)

    def subscribe(self, destination, ack=None, sub_id=None,
                  message_handler=None):
        if ack is None:
            ack = AckMode.AUTO

        if message_handler is None:
            message_handler = lambda sub, frame: None

        if sub_id is None:
            sub_id = str(uuid4())

        self.queue_frame(Frame(
            Command.SUBSCRIBE,
            {
                "destination": destination,
                "ack": ack,
                "id": sub_id
            }
        ))

        sub = Subscription(self, destination, sub_id, ack, message_handler)
        self._subscriptions[sub_id] = sub

        return sub

    def unsubscribe(self, sub):
        try:
            del self._subscriptions[sub.id]
        except KeyError:
            self.log.warning('No subscription with %s id', sub.id)
        else:
            self.queue_frame(Frame(Command.UNSUBSCRIBE,
                                   {"id": sub.id}))

    def _process_disconnect(self, frame, dispatcher):
        r_id = frame.headers[Headers.RECEIPT]
        if not r_id:
            self.log.debug("No receipt id for disconnect frame")
            # it is not mandatory to send receipt frame
            return

        headers = {Headers.RECEIPT_ID: r_id}
        self.queue_frame(Frame(Command.RECEIPT, headers))

    def restore_subscriptions(self):
        subs = [sub for sub in six.viewvalues(self._subscriptions)]
        self._subscriptions.clear()

        for sub in subs:
            self.subscribe(sub.destination,
                           message_handler=sub.message_handler)


class StompClient(object):
    log = logging.getLogger("jsonrpc.AsyncoreClient")

    """
    We create a client by providing socket used for communication.
    Reactor object responsible for processing I/O and flag
    which tells client whether it should manage reactor's
    life cycle (by default set to True).
    """
    def __init__(self, sock, reactor, owns_reactor=True,
                 incoming_heartbeat=DEFAULT_INCOMING,
                 outgoing_heartbeat=DEFAULT_OUTGOING,
                 nr_retries=NR_RETRIES,
                 reconnect_interval=RECONNECT_INTERVAL):
        self._reactor = reactor
        self._owns_reactor = owns_reactor
        self._messageHandler = None
        self._socket = sock

        self._aclient = AsyncClient(
            incoming_heartbeat, outgoing_heartbeat, nr_retries,
            reconnect_interval)
        self._stompConn = StompConnection(
            self,
            self._aclient,
            sock,
            reactor
        )
        self._stompConn.set_heartbeat(outgoing_heartbeat, incoming_heartbeat)
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

    def unsubscribe(self, sub):
        self._aclient.unsubscribe(sub)

    def send(self, message, destination=SUBSCRIPTION_ID_RESPONSE,
             headers=None):
        self.log.debug("Sending response")

        if self._stompConn.is_closed():
            self._aclient.resend(destination, message, headers)

        self._aclient.send(
            destination,
            message,
            headers
        )
        self._reactor.wakeup()

    def close(self):
        self._stompConn.close()
        if self._owns_reactor:
            self._reactor.stop()


class ClientRpcTransportAdapter(object):
    def __init__(self, request_queue, response_queue, client):
        self._client = client
        self._message_handler = lambda arg: None
        self._request_queue = request_queue
        self._response_queue = response_queue
        self._subs = {}

        # Subscribe to main RPC queue
        self.subscribe(
            response_queue,
            lambda msg: self._message_handler(msg)
        )

    """
    In order to process message we need to set message
    handler which is responsible for processing jsonrpc
    content of the message. Currently this function is
    called only from JsonRpcClient to set the callback.
    """
    def set_message_handler(self, handler):
        """
        Set a callback which handles messages received
        from the main RPC queue.

        :param handler: Callback to handle incoming messages
        :type handler: function (string) -> ()
        """
        self._message_handler = handler

    def send(self, data, destination=None, flow_id=None):
        if not destination:
            destination = self._request_queue

        headers = {
            "content-type": "application/json",
            "reply-to": self._response_queue,
        }

        if flow_id is not None:
            headers[Headers.FLOW_ID] = flow_id

        self._client.send(
            data,
            destination,
            headers,
        )

    def subscribe(self, queue_name, callback):
        """
        Subscribe to a queue and receive any messages sent to it.

        :param queue_name: Name of the queue
        :param callback: Function that is called on receiving a message
        :type callback: function (string) -> ()

        :return: Id of the subscription
        """

        sub_id = uuid4()
        self._subs[sub_id] = self._client.subscribe(
            queue_name,
            sub_id=str(sub_id),
            message_handler=lambda sub, frame: callback(frame.body)
        )

        return sub_id

    def unsubscribe(self, sub):
        """
        Unsubscribes and stops receiving messages.

        :param sub: Id of the subscription, returned from subscribe()
        """

        self._client.unsubscribe(self._subs[sub])
        del self._subs[sub]

    def close(self):
        for sub in self._subs.values():
            self._client.unsubscribe(sub)

        self._client.close()


def StompRpcClient(stomp_client, request_queue, response_queue):
    return JsonRpcClient(
        ClientRpcTransportAdapter(
            request_queue,
            response_queue,
            stomp_client,
        )
    )


def SimpleClient(host, port=54321, ssl=True,
                 incoming_heartbeat=DEFAULT_INCOMING,
                 outgoing_heartbeat=DEFAULT_OUTGOING,
                 nr_retries=NR_RETRIES,
                 reconnect_interval=RECONNECT_INTERVAL):
    """
    Returns JsonRpcClient able to receive jsonrpc messages and notifications.
    It is required to provide a host where we want to connect, port and whether
    we want to use ssl (True by default). Other settings use defaults and if
    there is a need to customize please use StandAloneRpcClient().
    """
    sslctx = None
    if ssl:
        sslctx = SSLContext(key_file=pki.KEY_FILE,
                            cert_file=pki.CERT_FILE,
                            ca_certs=pki.CA_FILE)
    return StandAloneRpcClient(host, port, "jms.topic.vdsm_requests",
                               str(uuid4()), sslctx, False,
                               incoming_heartbeat, outgoing_heartbeat,
                               nr_retries, reconnect_interval)


def StandAloneRpcClient(host, port, request_queue, response_queue,
                        sslctx=None, lazy_start=True,
                        incoming_heartbeat=DEFAULT_INCOMING,
                        outgoing_heartbeat=DEFAULT_OUTGOING,
                        nr_retries=NR_RETRIES,
                        reconnect_interval=RECONNECT_INTERVAL):
    """
    Returns JsonRpcClient able to receive jsonrpc messages and notifications.
    It is required to provide host and port where we want to connect and
    request and response queues that we want to use during communication.
    We can provide ssl context if we want to secure connection.
    """
    reactor = Reactor()

    def start():
        thread = concurrent.thread(reactor.process_requests,
                                   name='Client %s:%s' % (host, port))
        thread.start()

    client = StompClient(utils.create_connected_socket(host, port, sslctx),
                         reactor, incoming_heartbeat=incoming_heartbeat,
                         outgoing_heartbeat=outgoing_heartbeat,
                         nr_retries=nr_retries,
                         reconnect_interval=reconnect_interval)

    jsonclient = JsonRpcClient(
        ClientRpcTransportAdapter(
            request_queue,
            response_queue,
            client)
    )

    if lazy_start:
        setattr(jsonclient, 'start', start)
    else:
        start()

    return jsonclient
