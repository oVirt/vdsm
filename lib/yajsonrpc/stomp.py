# Copyright 2014-2017 Red Hat, Inc.
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
import logging
import six
import socket
from uuid import uuid4
from collections import deque
from threading import Event
from . import CALL_TIMEOUT

from vdsm.common import time
import re

DEFAULT_INTERVAL = 30

SUBSCRIPTION_ID_REQUEST = "jms.topic.vdsm_requests"
SUBSCRIPTION_ID_RESPONSE = "jms.topic.vdsm_responses"

_RE_ESCAPE_SEQUENCE = re.compile(r"\\(.)")

_RE_ENCODE_CHARS = re.compile(r"[\r\n\\:]")

_EC_DECODE_MAP = {
    r"\\": "\\",
    r"\r": "\r",
    r"\n": "\n",
    r"\c": ":",
}

_EC_ENCODE_MAP = {
    ":": "\c",
    "\\": "\\\\",
    "\r": "\\r",
    "\n": "\\n",
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
        if six.PY3 or (six.PY2 and isinstance(body, unicode)):
            body = body.encode('utf-8')

        self.body = body

    def encode(self):
        body = self.body
        # We do it here so we are sure header is up to date
        if body is not None:
            self.headers["content-length"] = len(body)

        data = [self.command, '\n']
        for key, value in self.headers.iteritems():
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
    # Make sure to leave this check before decoding as ':' can appear in the
    # value after decoding using \c
    if ":" in s:
        raise ValueError("Contains illigal charachter `:`")

    try:
        s = _RE_ESCAPE_SEQUENCE.sub(
            lambda m: _EC_DECODE_MAP[m.group(0)],
            s,
        )
    except KeyError as e:
        raise ValueError("Containes invalid escape squence `\\%s`" % e.args[0])

    if six.PY2 or (six.PY3 and isinstance(s, bytes)):
        s = s.decode('utf-8')
    return s


def encodeValue(s):
    if six.PY3 or (six.PY2 and isinstance(s, unicode)):
        s = s.encode('utf-8')
    elif isinstance(s, int):
        s = str(s)
    elif not isinstance(s, str):
        raise ValueError('Unable to encode non-string values')

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
                 clock=time.monotonic_time):
        self._frame_handler = frame_handler
        self.connection = connection
        self._bufferSize = bufferSize
        self._parser = Parser()
        self._outbuf = None
        self._incoming_heartbeat_in_milis = 0
        self._outgoing_heartbeat_in_milis = 0
        self._clock = clock

    def setHeartBeat(self, outgoing, incoming=0):
        if incoming:
            self._update_incoming_heartbeat()
            self._incoming_heartbeat_in_milis = incoming

        self._update_outgoing_heartbeat()
        self._outgoing_heartbeat_in_milis = outgoing

    def handle_connect(self, dispatcher):
        self._outbuf = None
        self._frame_handler.handle_connect(self)

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

    def handle_timeout(self):
        self._frame_handler.handle_timeout(self)

    def popFrame(self):
        return self._parser.popFrame()

    def _update_outgoing_heartbeat(self):
        self._lastOutgoingTimeStamp = self._clock()

    def _update_incoming_heartbeat(self):
        self._lastIncomingTimeStamp = self._clock()

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

    def next_check_interval(self):
        if self._incoming_heartbeat_expiration_interval() < 0:
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
        return True

    def _milis(self):
        return int(round(self._clock() * 1000))

    def handle_close(self, dispatcher):
        self.connection.close()


class AsyncClient(object):
    log = logging.getLogger("yajsonrpc.protocols.stomp.AsyncClient")

    def __init__(self, incoming_heartbeat=5000, outgoing_heartbeat=0):
        self._connected = Event()
        self._incoming_heartbeat = incoming_heartbeat
        self._outgoing_heartbeat = outgoing_heartbeat
        self._outbox = deque()
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

    @property
    def has_outgoing_messages(self):
        return (len(self._outbox) > 0)

    def peek_message(self):
        return self._outbox[0]

    def pop_message(self):
        return self._outbox.popleft()

    def getLastError(self):
        return self._error

    def handle_connect(self):
        # TODO : reset subscriptions
        # We use appendleft to make sure this is the first frame we send in
        # case of a reconnect
        self._outbox.appendleft(Frame(
            Command.CONNECT,
            {
                Headers.ACCEPT_VERSION: "1.2",
                Headers.HEARTBEAT: "%d,%d" % (self._outgoing_heartbeat,
                                              self._incoming_heartbeat),
            }
        ))

    def handle_frame(self, dispatcher, frame):
        self._commands[frame.command](frame, dispatcher)

    def _process_connected(self, frame, dispatcher):
        self._connected.set()

        self.log.debug("Stomp connection established")

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

    def send(self, destination, data="", headers=None):
        final_headers = {"destination": destination}
        if headers is not None:
            final_headers.update(headers)
        frame = Frame(Command.SEND, final_headers, data)
        if not self._connected.wait(timeout=CALL_TIMEOUT):
            raise StompError(frame, "Timeout occured during connecting")

        self.queue_frame(frame)

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

        sub = _Subscription(self, destination, sub_id, ack, message_handler)
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
            self.subscribe(sub.destination)


class _Subscription(object):

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

    def unsubscribe(self):
        self._client.unsubscribe(self)
        self._valid = False
