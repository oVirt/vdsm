# Copyright 2014-2019 Red Hat, Inc.
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

from __future__ import absolute_import
from __future__ import division
import logging
import six
import socket
from collections import deque

from vdsm.common import api
from vdsm.common import pki
from vdsm.common import time
from vdsm.sslutils import CLIENT_PROTOCOL, SSLSocket, SSLContext
import re

DEFAULT_INTERVAL = 30
RECONNECT_INTERVAL = 2

SUBSCRIPTION_ID_REQUEST = "jms.topic.vdsm_requests"
SUBSCRIPTION_ID_RESPONSE = "jms.topic.vdsm_responses"

DEFAULT_INCOMING = 30000
DEFAULT_OUTGOING = 0
NR_RETRIES = 1

# This is the value used by engine
GRACE_PERIOD_FACTOR = 0.2

# https://stomp.github.io/stomp-specification-1.2.html#Value_Encoding
_RE_ESCAPE_SEQUENCE = re.compile(br"\\(.)")

_RE_ENCODE_CHARS = re.compile(br"[\r\n\\:]")

_EC_DECODE_MAP = {
    br"\\": b"\\",
    br"\r": b"\r",
    br"\n": b"\n",
    br"\c": b":",
}

_EC_ENCODE_MAP = {
    b":": b"\\c",
    b"\\": b"\\\\",
    b"\r": b"\\r",
    b"\n": b"\\n",
}


class Command(object):
    MESSAGE = "MESSAGE"
    SEND = "SEND"
    SUBSCRIBE = "SUBSCRIBE"
    UNSUBSCRIBE = "UNSUBSCRIBE"
    CONNECT = "CONNECT"
    CONNECTED = "CONNECTED"
    ERROR = "ERROR"
    RECEIPT = "RECEIPT"
    DISCONNECT = "DISCONNECT"


class Headers(object):
    CONTENT_LENGTH = "content-length"
    CONTENT_TYPE = "content-type"
    FLOW_ID = "ovirtCorrelationId"
    SUBSCRIPTION = "subscription"
    RECEIPT = "receipt"
    RECEIPT_ID = "receipt-id"
    DESTINATION = "destination"
    ACCEPT_VERSION = "accept-version"
    REPLY_TO = "reply-to"
    HEARTBEAT = "heart-beat"


COMMANDS = tuple([command for command in dir(Command)
                  if not command.startswith('_')])


class AckMode(object):
    AUTO = "auto"


class StompError(RuntimeError):
    def __init__(self, frame, message):
        self.frame = frame
        self.message = message

    def __str__(self):
        return "Error in frame %s: %s" % (self.frame, self.message)


class Disconnected(RuntimeError):
    pass


class _HeartBeatFrame(object):
    def encode(self):
        return "\n"

# There is no reason to have multiple instances
_heartBeatFrame = _HeartBeatFrame()


class Frame(object):
    __slots__ = ("headers", "command", "body")

    def __init__(self, command="", headers=None, body=None):
        self.command = command
        if headers is None:
            headers = {}

        self.headers = headers
        if isinstance(body, six.text_type):
            body = body.encode('utf-8')

        self.body = body

    def encode(self):
        body = self.body
        # We do it here so we are sure header is up to date
        if body is not None:
            self.headers["content-length"] = len(body)

        data = [self.command, '\n']
        for key, value in six.viewitems(self.headers):
            data.append(encodeValue(key))
            data.append(":")
            data.append(encodeValue(value))
            data.append("\n")

        data.append('\n')
        if body is not None:
            data.append(body)

        data.append("\0")
        return ''.join(data)

    def __repr__(self):
        return "<StompFrame command=%s>" % (repr(self.command))

    def copy(self):
        return Frame(self.command, self.headers.copy(), self.body)


def decodeValue(s):
    if not isinstance(s, six.binary_type):
        raise ValueError("Unable to decode non-binary values")

    # Make sure to leave this check before decoding as ':' can appear in the
    # value after decoding using \c
    if b":" in s:
        raise ValueError("Contains illegal character ':'")

    try:
        s = _RE_ESCAPE_SEQUENCE.sub(
            lambda m: _EC_DECODE_MAP[m.group(0)],
            s,
        )
    except KeyError as e:
        raise ValueError("Contains invalid escape sequence '\\%s'" % e.args[0])

    return s.decode("utf-8")


def encodeValue(s):
    if isinstance(s, six.text_type):
        s = s.encode("utf-8")
    # TODO: Remove handling ints as 'decodeValue'
    #       doesn't do the reverse conversion
    elif isinstance(s, int):
        s = str(s).encode("utf-8")
    elif not isinstance(s, six.binary_type):
        raise ValueError("Unable to encode non-string values")

    return _RE_ENCODE_CHARS.sub(lambda m: _EC_ENCODE_MAP[m.group(0)], s)


class Parser(object):
    _STATE_CMD = "Parsing command"
    _STATE_HEADER = "Parsing headers"
    _STATE_BODY = "Receiving body"

    def __init__(self):
        self._states = {
            self._STATE_CMD: self._parse_command,
            self._STATE_HEADER: self._parse_header,
            self._STATE_BODY: self._parse_body}
        self._frames = deque()
        self._change_state(self._STATE_CMD)
        self._contentLength = -1
        self._flush()

    def _change_state(self, new_state):
        self._state = new_state
        self._state_cb = self._states[new_state]

    def _flush(self):
        self._buffer = ""

    def _write_buffer(self, buff):
        self._buffer += buff

    def _get_buffer(self):
        return self._buffer

    def _handle_terminator(self, term):
        res, sep, rest = self._buffer.partition(term)
        if not sep:
            return None

        self._buffer = rest

        return res

    def _parse_command(self):
        cmd = self._handle_terminator('\n')
        if cmd is None:
            return False

        if len(cmd) > 0 and cmd[-1] == '\r':
            cmd = cmd[:-1]

        if cmd == "":
            return True

        cmd = decodeValue(cmd)
        self._tmpFrame = Frame(cmd)

        self._change_state(self._STATE_HEADER)
        return True

    def _parse_header(self):
        header = self._handle_terminator('\n')
        if header is None:
            return False

        if len(header) > 0 and header[-1] == '\r':
            header = header[:-1]

        headers = self._tmpFrame.headers
        if header == "":
            self._contentLength = int(headers.get('content-length', -1))
            self._change_state(self._STATE_BODY)
            return True

        key, value = header.split(":", 1)
        key = decodeValue(key)
        value = decodeValue(value)

        # If a client or a server receives repeated frame header entries, only
        # the first header entry SHOULD be used as the value of header entry.
        # Subsequent values are only used to maintain a history of state
        # changes of the header and MAY be ignored.
        headers.setdefault(key, value)

        return True

    def _pushFrame(self):
        self._frames.append(self._tmpFrame)
        self._change_state(self._STATE_CMD)
        self._tmpFrame = None
        self._contentLength = -1

    def _parse_body(self):
        if self._contentLength >= 0:
            return self._parse_body_length()
        else:
            return self._parse_body_terminator()

    def _parse_body_terminator(self):
        body = self._handle_terminator('\0')
        if body is None:
            return False

        self._tmpFrame.body = body
        self._pushFrame()
        return True

    def _parse_body_length(self):
        buf = self._get_buffer()
        cl = self._contentLength
        ndata = len(buf)
        if ndata < (cl + 1):
            return False

        if buf[cl] != "\0":
            raise RuntimeError("Frame end is missing \\0")

        self._flush()
        self._write_buffer(buf[cl + 1:])
        body = buf[:cl]

        self._tmpFrame.body = body
        self._pushFrame()

        return True

    @property
    def pending(self):
        return len(self._frames)

    def parse(self, data):
        self._write_buffer(data)
        while self._state_cb():
            pass

    def popFrame(self):
        try:
            return self._frames.popleft()
        except IndexError:
            return None


class AsyncDispatcher(object):
    log = logging.getLogger("stomp.AsyncDispatcher")

    """
    Uses asyncore dispatcher to handle regular messages and heartbeats.
    It accepts frame handler which abstracts message processing and a
    connection. Abstract frame handler should look like:

    class abstract_frame_handler(object):

        Performs any required action after a connection is established
        def handle_connect(self)

        Process received frame
        def handle_frame(self, frame)

        Returns response frame to be sent
        def peek_message(self)

        Returns Ture if there are messages to be sent
        def has_outgoing_messages(self)

        Queues a frame to be sent
        def queue_frame(self, frame)

    There are two implementations available:
    - StompAdapterImpl - responsible for server side
    - AsyncClient - responsible for client side
    """
    def __init__(self, connection, frame_handler, bufferSize=4096,
                 clock=time.monotonic_time, count=0):
        self._frame_handler = frame_handler
        self.connection = connection
        self._bufferSize = bufferSize
        self._parser = Parser()
        self._outbuf = None
        self._incoming_heartbeat_in_milis = 0
        self._outgoing_heartbeat_in_milis = 0
        self._reconnect_interval = 0
        self._nr_retries = 0
        self._count = count
        if hasattr(self._frame_handler, "nr_retries"):
            self._nr_retries = self._frame_handler.nr_retries
        if hasattr(self._frame_handler, "reconnect_interval"):
            self._reconnect_interval = self._frame_handler.reconnect_interval
        self._on_timeout = False
        self._clock = clock
        self._on_wait = False

        if hasattr(self._frame_handler, "reconnect_interval"):
            self.set_reconnect_interval(self._frame_handler.reconnect_interval)

    def setHeartBeat(self, outgoing, incoming=0):
        if incoming:
            self._update_incoming_heartbeat()
            self._incoming_heartbeat_in_milis = incoming

        self._update_outgoing_heartbeat()
        self._outgoing_heartbeat_in_milis = outgoing

    def set_reconnect_interval(self, reconnect_interval):
        self._reconnect_interval = reconnect_interval
        self._update_reconnect_time()

    def handle_connect(self, dispatcher):
        self.log.debug("managed to connect successfully.")
        self._outbuf = None
        self._count = 0
        self._on_timeout = False
        self._update_reconnect_time()
        self._frame_handler.handle_connect()

    def handle_read(self, dispatcher):
        parser = self._parser
        pending = getattr(dispatcher.socket, 'pending', lambda: 0)
        todo = self._bufferSize

        while todo:
            try:
                data = dispatcher.recv(todo)
            except socket.error:
                dispatcher.handle_error()
                return

            # When a socket is closed data is not available so we do not
            # need to parse it.
            if not data:
                return
            parser.parse(data)
            todo = pending()

        while parser.pending > 0:
            self._frame_handler.handle_frame(self, parser.popFrame())

        if self._incoming_heartbeat_in_milis:
            self._update_incoming_heartbeat()

    def handle_error(self, dispatcher):
        self.log.debug("Communication error occurred.")
        self._frame_handler.handle_error(self)

    def handle_timeout(self):
        self._on_timeout = True

        if not self._on_wait:
            self._start = self._clock() + self._reconnect_interval
            self._on_wait = True
            return

        if self._count >= self._nr_retries:
            self._on_timeout = False
            self.connection.close()
            return

        self._update_reconnect_time()
        self._count += 1
        self._on_wait = False
        self.connection.close()
        self._frame_handler.handle_timeout(self)

    def popFrame(self):
        return self._parser.popFrame()

    def _update_outgoing_heartbeat(self):
        self._lastOutgoingTimeStamp = self._clock()

    def _update_incoming_heartbeat(self):
        self._lastIncomingTimeStamp = self._clock()

    def _update_reconnect_time(self):
        self._lastReconnectTimeStamp = self._clock()

    def _outgoing_heartbeat_expiration_interval(self):
        if self._outgoing_heartbeat_in_milis == 0:
            return DEFAULT_INTERVAL
        since_last_update = (self._clock() - self._lastOutgoingTimeStamp)
        return (self._outgoing_heartbeat_in_milis / 1000.0) - since_last_update

    def _incoming_heartbeat_expiration_interval(self):
        if self._incoming_heartbeat_in_milis == 0:
            return DEFAULT_INTERVAL
        since_last_update = (self._clock() - self._lastIncomingTimeStamp)
        return (self._incoming_heartbeat_in_milis / 1000.0) - since_last_update

    def _reconnect_expiration_interval(self):
        if not self._on_timeout or self._reconnect_interval == 0:
            return DEFAULT_INTERVAL
        since_last_update = (self._clock() - self._lastReconnectTimeStamp)
        return self._reconnect_interval - since_last_update

    def next_check_interval(self):
        if self._on_wait:
            if self._clock() > self._start:
                self.handle_timeout()
            return self._reconnect_interval

        if self._reconnect_expiration_interval() <= 0 or \
                self._incoming_heartbeat_expiration_interval() <= 0:
            self.handle_timeout()

        return max(self._outgoing_heartbeat_expiration_interval(), 0)

    def handle_write(self, dispatcher):
        while True:
            if self._outbuf is None:
                try:
                    frame = self._frame_handler.peek_message()
                except IndexError:
                    return

                self._outbuf = frame.encode()

            data = self._outbuf
            numSent = dispatcher.send(data)
            if numSent == 0:
                # want to resend
                resend = self._frame_handler.peek_message()
                if resend.command == Command.SEND:
                    self._frame_handler.queue_resend(resend)
                return

            self._update_outgoing_heartbeat()
            if numSent < len(data):
                self._outbuf = data[numSent:]
                return

            self._outbuf = None
            self._frame_handler.pop_message()

    def writable(self, dispatcher):
        if self._frame_handler.has_outgoing_messages:
            return True

        if self._outbuf is not None:
            return True

        if (self.next_check_interval() == 0):
            self._frame_handler.queue_frame(_heartBeatFrame)
            return True

        return False

    def readable(self, dispatcher):
        return not self._on_timeout

    def _milis(self):
        return int(round(self._clock() * 1000))  # pylint: disable=W1633

    def handle_close(self, dispatcher):
        if not self._on_timeout:
            self._frame_handler.handle_close(self)


class Subscription(object):

    def __init__(self, client, destination, subid, ack, message_handler):
        self._ack = ack
        self._subid = subid
        self._client = client
        self._valid = True
        self._message_handler = message_handler
        self._destination = destination

    def handle_message(self, frame):
        self._message_handler(self, frame)

    """
    In order to process message we need to set message
    handler which is responsible for processing jsonrpc
    content of the message. Currently there are 2 handlers:
    JsonRpcClient and JsonRpcServer.
    """
    def set_message_handler(self, handler):
        self._message_handler = handler

    @property
    def id(self):
        return self._subid

    @property
    def destination(self):
        return self._destination

    @property
    def client(self):
        return self._client

    @property
    def message_handler(self):
        return self._message_handler

    def unsubscribe(self):
        self._client.unsubscribe(self)
        self._valid = False


class StompConnection(object):

    def __init__(self, server, aclient, sock, reactor):
        self._reactor = reactor
        self._server = server
        self._messageHandler = None

        self._async_client = aclient
        self._server_host, self._server_port = sock.getsockname()[:2]
        self._sslctx = None
        if isinstance(sock, SSLSocket):
            self._sslctx = self._create_ssl_context()
        self.initiate_connection(sock)

    def initiate_connection(self, sock):
        self._dispatcher = self._reactor.create_dispatcher(
            sock, AsyncDispatcher(self, self._async_client))
        self._client_host = self._dispatcher.addr[0]
        self._client_port = self._dispatcher.addr[1]

    def _create_ssl_context(self):
        return SSLContext(key_file=pki.KEY_FILE, cert_file=pki.CERT_FILE,
                          ca_certs=pki.CA_FILE, protocol=CLIENT_PROTOCOL)

    def send_raw(self, msg):
        self._async_client.queue_frame(msg)
        self._reactor.wakeup()

    def setTimeout(self, timeout):
        self._dispatcher.socket.settimeout(timeout)

    @property
    def dispatcher(self):
        return self._dispatcher

    def connect(self):
        pass

    def reconnect(self, count, on_timeout):
        self._dispatcher = self._reactor.reconnect(
            (self._client_host, self._client_port), self._sslctx,
            AsyncDispatcher(self, self._async_client, count=count))

    def set_heartbeat(self, outgoing, incoming):
        self._dispatcher.set_heartbeat(outgoing, incoming)

    def close(self):
        self._dispatcher.close()
        if hasattr(self._async_client, 'remove_subscriptions'):
            self._async_client.remove_subscriptions()

    def get_local_address(self):
        return self._dispatcher.socket.getsockname()[0]

    def set_message_handler(self, msgHandler):
        self._messageHandler = msgHandler
        self._dispatcher.handle_read_event()

    def handleMessage(self, data, flow_id):
        if self._messageHandler is not None:
            context = api.Context(flow_id, self._client_host,
                                  self._client_port)
            self._messageHandler((self._server, self.get_local_address(),
                                  context, data))

    def is_closed(self):
        return not self._dispatcher.connected
