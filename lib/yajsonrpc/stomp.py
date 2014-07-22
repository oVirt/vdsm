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
import cStringIO
from threading import Timer, Event
from uuid import uuid4
from collections import deque
import time

from betterAsyncore import Dispatcher
import asyncore
import re

_RE_ESCAPE_SEQUENCE = re.compile(r"\\(.)")

_EC_DECODE_MAP = {
    r"\\": "\\",
    r"r": "\r",
    r"n": "\n",
    r"c": ":",
}

_ESCAPE_CHARS = (('\\\\', '\\'), ('\\r', '\r'), ('\\n', '\n'), ('\\c', ':'))


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

        data = self.command + '\n'
        data += '\n'.join(["%s:%s" % (encodeValue(key), encodeValue(value))
                          for key, value in self.headers.iteritems()])
        data += '\n\n'
        if body is not None:
            data += body

        data += "\0"
        return data

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
        return _RE_ESCAPE_SEQUENCE.sub(
            lambda m: _EC_DECODE_MAP[m.groups()[0]],
            s
        )
    except KeyError as e:
        raise ValueError("Containes invalid escape squence `\\%s`" % e.args[0])


def encodeValue(s):
    if not isinstance(s, (str, unicode)):
        s = str(s)
    for escaped, raw in _ESCAPE_CHARS:
        s = s.replace(raw, escaped)

    if isinstance(s, unicode):
        s = s.encode('utf-8')

    return s


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
        self._state = self._STATE_CMD
        self._contentLength = -1
        self._flush()

    def _flush(self):
        self._buffer = cStringIO.StringIO()

    def _handle_terminator(self, term):
        if term not in self._buffer.getvalue():
            return None

        data = self._buffer.getvalue()
        res, rest = data.split(term, 1)
        self._flush()
        self._buffer.write(rest)

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

        self._state = self._STATE_HEADER
        return True

    def _parse_header(self):
        header = self._handle_terminator('\n')
        if header is None:
            return False

        headers = self._tmpFrame.headers
        if len(header) > 0 and header[-1] == '\r':
            header = header[:-1]

        if header == "":
            self._contentLength = int(headers.get('content-length', -1))
            self._state = self._STATE_BODY
            return True

        key, value = header.split(":", 1)
        key = decodeValue(key)
        value = decodeValue(value)

        # If a client or a server receives repeated frame header entries, only
        # the first header entry SHOULD be used as the value of header entry.
        # Subsequent values are only used to maintain a history of state
        # changes of the header and MAY be ignored.
        if key not in headers:
            headers[key] = value

        return True

    def _pushFrame(self):
        self._frames.append(self._tmpFrame)
        self._state = self._STATE_CMD
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
        buf = self._buffer
        cl = self._contentLength
        ndata = buf.tell()
        if ndata < cl:
            return False

        remainingBytes = 0
        self._flush()
        body = buf.getvalue()
        self._buffer.write(body[cl + 1:])
        body = body[:cl]

        if remainingBytes == 0:
            self._tmpFrame.body = body
            self._pushFrame()

        return True

    @property
    def pending(self):
        return len(self._frames)

    def parse(self, data):
        states = self._states
        self._buffer.write(data)
        while states[self._state]():
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
        s = time.time()
        duration = 0
        while timeout is None or duration < timeout:
            try:
                return self._inbox.popleft()
            except IndexError:
                td = timeout - duration if timeout is not None else None
                self.process(td)
                duration = time.time() - s

        return None

    def put_subscribe(self, destination, ack=None):
        subid = self._aclient.subscribe(self._adisp, destination, ack)
        sub = Subsciption(self, subid, ack)
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
        s = time.time()
        while ((timeout is None or duration < timeout) and
               (disp.writable() or not self._connected.isSet())):
                td = timeout - duration if timeout is not None else None
                self.process(td)
                duration = time.time() - s

    def gettimout(self):
        return self._timeout

    def settimeout(self, value):
        self._timeout = value


class AsyncDispatcher(object):
    log = logging.getLogger("stomp.AsyncDispatcher")

    def __init__(self, frameHandler, bufferSize=1024):
        self._frameHandler = frameHandler
        self._bufferSize = bufferSize
        self._parser = Parser()
        self._outbox = deque()
        self._outbuf = None
        self._outgoingHeartBeat = 0

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

        self._lastOutgoingTimeStamp = self._milis()
        self._outgoingHeartBeat = outgoing

    def handle_connect(self, dispatcher):
        self._frameHandler.handle_connect(self)

    def handle_read(self, dispatcher):
        pending = self._bufferSize
        while pending:
            try:
                data = dispatcher.recv(pending)
            except socket.error:
                dispatcher.handle_error()
                return

            try:
                pending = dispatcher.socket.pending()
            except AttributeError:
                pending = 0
                pass

            parser = self._parser

            if data is not None:
                parser.parse(data)

        frameHandler = self._frameHandler
        if hasattr(frameHandler, "handle_frame"):
            while parser.pending > 0:
                frameHandler.handle_frame(self, parser.popFrame())

    def popFrame(self):
        return self._parser.popFrame()

    def handle_write(self, dispatcher):
        if self._outbuf is None:
            try:
                frame = self._outbox.popleft()
            except IndexError:
                return

            self._outbuf = frame.encode()

        data = self._outbuf
        numSent = dispatcher.send(data)
        self._lastOutgoingTimeStamp = self._milis()
        if numSent == len(data):
            self._outbuf = None
        else:
            self._outbuf = data[numSent:]

    def send_raw(self, frame):
        self._queueFrame(frame)

    def writable(self, dispatcher):
        if len(self._outbox) > 0 or self._outbuf is not None:
            return True

        if (self._outgoingHeartBeat > 0
            and ((self._milis() - self._lastOutgoingTimeStamp)
                 > self._outgoingHeartBeat)):
            self._queueFrame(_heartBeatFrame)
            return True

        return False

    def readable(self, dispatcher):
        return True

    def _milis(self):
        return int(round(time.time() * 1000))


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


class Subsciption(object):
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
