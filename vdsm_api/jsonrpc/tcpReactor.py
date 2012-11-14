# Copyright (C) 2012 Saggi Mizrahi, Red Hat Inc.
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
import errno
import struct
import logging
import socket
from select import poll, POLLIN, POLLPRI, POLLERR, POLLHUP, POLLOUT

from vdsm import utils

_Size = struct.Struct("!Q")


class TCPConnection(object):
    def __init__(self, conn, buffsize=(2 ** 20)):
        self._conn = conn
        conn.setblocking(False)
        self.buffsize = buffsize
        self._buffer = ""
        self._sendData = []

    def fileno(self):
        return self._conn.fileno()

    def addSendData(self, data):
        self._sendData.append(data)

    def hasSendData(self):
        return len(self._sendData) > 0

    def processOutput(self):
        try:
            if (len(self._sendData) > 0):
                data = self._sendData[0]
                bsent = self._conn.send(data)
                if bsent < len(data):
                    self._sendData[0] = data[bsent:]
                else:
                    self._sendData.pop(0)
        except (OSError, IOError) as e:
            if e.errno not in (errno.EINTR, errno.EAGAIN):
                raise

    def processInput(self):
        try:
            self._buffer += self._conn.recv(self.buffsize - len(self._buffer))
        except (OSError, IOError) as e:
            if e.errno not in (errno.EINTR, errno.EAGAIN):
                raise

        buffLen = len(self._buffer)
        if buffLen < _Size.size:
            return None

        msgLen = _Size.unpack(self._buffer[:_Size.size])[0]

        # Message to big
        if msgLen > self.buffsize:
            self._conn.close()
            self._sendData = []

        if (buffLen - _Size.size) < msgLen:
            return None

        msgStart = _Size.size
        msgEnd = msgLen + _Size.size
        res = self._buffer[msgStart:msgEnd]
        self._buffer = self._buffer[msgEnd:]
        return res


class TCPMessageContext(object):

    def __init__(self, server, conn, data):
        self._server = server
        self._conn = conn
        self._data = data

    @property
    def data(self):
        return self._data

    def sendReply(self, data):
        self._server.sendReply(self, data)


class TCPReactor(object):
    log = logging.getLogger("jsonrpc.TCPReactor")

    def __init__(self, address, messageHandler):
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._addr = address
        self._messageHandler = messageHandler
        self._inputEvent = utils.PollEvent()
        # TODO: Close on exec
        self._sock.bind(self._addr)

    def start_listening(self):
        sock = self._sock
        sock.listen(10)

    def process_requests(self):
        sock = self._sock
        poller = poll()
        poller.register(sock, POLLIN | POLLPRI)
        poller.register(self._inputEvent, POLLIN | POLLPRI)
        connections = {}
        self.log.debug("Starting to accept clients")
        sockFD = sock.fileno()
        while True:
            for fd, conn in connections.iteritems():
                if conn.hasSendData():
                    poller.modify(fd, (POLLIN | POLLPRI | POLLOUT))
                else:
                    poller.modify(fd, (POLLIN | POLLPRI))

            for fd, ev in poller.poll():
                if ev & (POLLERR | POLLHUP):
                    if fd == sockFD:
                        self.log.info("Listening socket closed, shutting down")
                        return

                    self.log.debug("Connection closed")
                    poller.unregister(fd)
                    del connections[fd]

                elif fd == self._inputEvent.fileno():
                    self._inputEvent.clear()

                elif fd == sockFD:
                    try:
                        conn, addr = sock.accept()
                    except (OSError, IOError):
                        continue

                    self.log.debug("Processing new connection")
                    connections[conn.fileno()] = TCPConnection(conn)
                    poller.register(conn, (POLLIN | POLLPRI))
                else:
                    conn = connections[fd]
                    if ev & (POLLIN | POLLPRI):
                        msg = conn.processInput()
                        while msg is not None:
                            ctx = TCPMessageContext(self, conn, msg)
                            self._messageHandler.handleMessage(ctx)
                            msg = conn.processInput()
                    if ev & POLLOUT:
                        conn.processOutput()

    def sendReply(self, ctx, message):
        conn = ctx._conn
        conn.addSendData(_Size.pack(len(message)) + message)
        self._inputEvent.set()

    def stop(self):
        self._sock.close()
