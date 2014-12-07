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
import socket
from threading import Timer, Event
from uuid import uuid4
from collections import deque

from betterAsyncore import Dispatcher
from vdsm.utils import monotonic_time
import asyncore
import re


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


class Command:
    MESSAGE = "MESSAGE"
    SEND = "SEND"
    SUBSCRIBE = "SUBSCRIBE"
    UNSUBSCRIBE = "UNSUBSCRIBE"
    CONNECT = "CONNECT"
    CONNECTED = "CONNECTED"
    ERROR = "ERROR"
    RECEIPT = "RECEIPT"


COMMANDS = tuple([command for command in dir(Command)
                  if not command.startswith('_')])


class AckMode:
    AUTO = "auto"


class StompError(RuntimeError):
    def __init__(self, frame):
        RuntimeError.__init__(self, frame.body)


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
        if isinstance(body, unicode):
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

    return s.decode('utf-8')


def encodeValue(s):
    if isinstance(s, unicode):
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


class Client(object):
    def __init__(self, sock=None):
        """
        Initialize the client.

        The socket parameter can be an already initialized socket. Should be
        used to pass specialized socket objects like SSL sockets.
        """
        if sock is None:
            sock = self._sock = socket.socket()
        else:
            self._sock = sock

        self._map = {}
        # Because we don't know how large the frames are
        # we have to use non bolocking IO
        sock.setblocking(False)

        # We have our own timeout for operations we
        # pretend to be synchronous (like connect).
        self._timeout = None
        self._connected = Event()
        self._subscriptions = {}

        self._aclient = None
        self._adisp = None

        self._inbox = deque()

    @property
    def outgoing(self):
        return self._adisp.outgoing

    def _registerSubscription(self, sub):
        self._subscriptions[sub.id] = sub

    def _unregisterSubscription(self, sub):
        del self._subscriptions[sub.id]

    @property
    def connected(self):
        return self._connected.isSet()

    def handle_connect(self, aclient, frame):
        self._connected.set()

    def handle_message(self, aclient, frame):
        self._inbox.append(frame)

    def process(self, timeout=None):
        if timeout is None:
            timeout = self._timeout

        asyncore.loop(use_poll=True, timeout=timeout, map=self._map, count=1)

    def connect(self, address, hostname):
        sock = self._sock

        self._aclient = AsyncClient(self, hostname)
        adisp = self._adisp = AsyncDispatcher(self._aclient)
        disp = self._disp = Dispatcher(adisp, sock, self._map)
        sock.setblocking(True)
        disp.connect(address)
        sock.setblocking(False)
        self.recv()  # wait for CONNECTED msg

        if not self._connected.isSet():
            sock.close()
            raise socket.error()

    def recv(self):
        timeout = self._timeout
        s = monotonic_time()
        duration = 0
        while timeout is None or duration < timeout:
            try:
                return self._inbox.popleft()
            except IndexError:
                td = timeout - duration if timeout is not None else None
                self.process(td)
                duration = monotonic_time() - s

        return None

    def put_subscribe(self, destination, ack=None):
        subid = self._aclient.subscribe(self._adisp, destination, ack)
        sub = _Subscription(self, subid, ack)
        self._registerSubscription(sub)
        return sub

    def put_send(self, destination, data="", headers=None):
        self._aclient.send(self._adisp, destination, data, headers)

    def put(self, frame):
        self._adisp.send_raw(frame)

    def send(self):
        disp = self._disp
        timeout = self._timeout
        duration = 0
        s = monotonic_time()
        while ((timeout is None or duration < timeout) and
               (disp.writable() or not self._connected.isSet())):
                td = timeout - duration if timeout is not None else None
                self.process(td)
                duration = monotonic_time() - s

    def gettimout(self):
        return self._timeout

    def settimeout(self, value):
        self._timeout = value


class AsyncDispatcher(object):
    log = logging.getLogger("stomp.AsyncDispatcher")

    def __init__(self, frameHandler, bufferSize=4096):
        self._frameHandler = frameHandler
        self._bufferSize = bufferSize
        self._parser = Parser()
        self._outbox = deque()
        self._outbuf = None
        self._outgoing_heartbeat_in_milis = 0

    def _queueFrame(self, frame):
        self._outbox.append(frame)

    @property
    def outgoing(self):
        n = len(self._outbox)
        if self._outbuf != "":
            n += 1

        return n

    def setHeartBeat(self, outgoing, incoming=0):
        if incoming != 0:
            raise ValueError("incoming heart-beat not supported")

        self._update_outgoing_heartbeat()
        self._outgoing_heartbeat_in_milis = outgoing

    def handle_connect(self, dispatcher):
        self._outbuf = None
        self._frameHandler.handle_connect(self)

    def handle_read(self, dispatcher):
        try:
            data = dispatcher.recv(self._bufferSize)
        except socket.error:
            dispatcher.handle_error()
            return

        parser = self._parser

        if data is not None:
            parser.parse(data)

        frameHandler = self._frameHandler
        if hasattr(frameHandler, "handle_frame"):
            while parser.pending > 0:
                frameHandler.handle_frame(self, parser.popFrame())

    def popFrame(self):
        return self._parser.popFrame()

    def _update_outgoing_heartbeat(self):
        self._lastOutgoingTimeStamp = monotonic_time()

    def _outgoing_heartbeat_expiration_interval(self):
        since_last_update = (monotonic_time() - self._lastOutgoingTimeStamp)
        return (self._outgoing_heartbeat_in_milis / 1000.0) - since_last_update

    def next_check_interval(self):
        if self._outgoing_heartbeat_in_milis == 0:
            return None

        return max(self._outgoing_heartbeat_expiration_interval(), 0)

    def handle_write(self, dispatcher):
        if self._outbuf is None:
            try:
                frame = self._outbox.popleft()
            except IndexError:
                return

            self._outbuf = frame.encode()

        data = self._outbuf
        numSent = dispatcher.send(data)
        self._update_outgoing_heartbeat()
        if numSent == len(data):
            self._outbuf = None
        else:
            self._outbuf = data[numSent:]

    def send_raw(self, frame):
        self._queueFrame(frame)

    def writable(self, dispatcher):
        if len(self._outbox) > 0 or self._outbuf is not None:
            return True

        if (self.next_check_interval() == 0):
            self._queueFrame(_heartBeatFrame)
            return True

        return False

    def readable(self, dispatcher):
        return True

    def _milis(self):
        return int(round(monotonic_time() * 1000))


class AsyncClient(object):
    log = logging.getLogger("yajsonrpc.protocols.stomp.AsyncClient")

    def __init__(self, frameHandler, hostname):
        self._hostname = hostname
        self._frameHandler = frameHandler
        self._connected = False
        self._error = None
        self._commands = {
            Command.CONNECTED: self._process_connected,
            Command.MESSAGE: self._process_message,
            Command.RECEIPT: self._process_receipt,
            Command.ERROR: self._process_error}

    @property
    def connected(self):
        return self._connected

    def getLastError(self):
        return self._error

    def handle_connect(self, dispatcher):
        hostname = self._hostname
        frame = Frame(
            Command.CONNECT,
            {"accept-version": "1.2",
             "host": hostname})

        dispatcher.send_raw(frame)

    def handle_frame(self, dispatcher, frame):
        self._commands[frame.command](frame, dispatcher)

    def _process_connected(self, frame, dispatcher):
        self._connected = True
        frameHandler = self._frameHandler
        if hasattr(frameHandler, "handle_connect"):
            frameHandler.handle_connect(self, frame)

        self.log.debug("Stomp connection established")

    def _process_message(self, frame, dispatcher):
        frameHandler = self._frameHandler

        if hasattr(frameHandler, "handle_message"):
            frameHandler.handle_message(self, frame)

    def _process_receipt(self, frame, dispatcher):
        self.log.warning("Receipt frame received and ignored")

    def _process_error(self, frame, dispatcher):
        raise StompError(frame)

    def send(self, dispatcher, destination, data="", headers=None):
        frame = Frame(
            Command.SEND,
            {"destination": destination},
            data)

        dispatcher.send_raw(frame)

    def subscribe(self, dispatcher, destination, ack=None):
        if ack is None:
            ack = AckMode.AUTO

        subscriptionID = str(uuid4())

        frame = Frame(
            Command.SUBSCRIBE,
            {"destination": destination,
             "ack": ack,
             "id": subscriptionID})

        dispatcher.send_raw(frame)

        return subscriptionID


class _Subscription(object):
    def __init__(self, client, subid, ack):
        self._ack = ack
        self._subid = subid
        self._client = client
        self._valid = True

    @property
    def id(self):
        return self._subid

    def unsubscribe(self):
        client = self._client
        subid = self._subid

        client._unregisterSubscription(self)

        frame = Frame(Command.UNSUBSCRIBE,
                      {"id": str(subid)})
        client.put(frame)
        self._valid = False

    def __del__(self):
        # Using a timer because unsubscribe action might involve taking locks.
        if self._valid:
            Timer(0, self.unsubscribe)
