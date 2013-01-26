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


class TCPClient(object):
    def __init__(self, reactor, sock):
        self._sock = sock
        self._conn = TCPConnection(sock)
        self._inbox = None
        self._reactor = reactor

    def _pushRecievedMessage(self, msg):
        try:
            self._inbox.put_nowait((self, msg))
        except AttributeError:
            # Inbox not set
            pass

    def fileno(self):
        return self._sock.fileno()

    def setInbox(self, queue):
        self._inbox = queue

    def send(self, message):
        self._conn.addSendData(_Size.pack(len(message)) + message)
        self._reactor.wakeup()

    def _processInput(self):
        msg = self._conn.processInput()
        while msg is not None:
            self._pushRecievedMessage(msg)
            msg = self._conn.processInput()

    def _processOutput(self):
        self._conn.processOutput()

    def _hasSendData(self):
        return self._conn.hasSendData()

    def close(self):
        self._sock.close()


class TCPListener(object):
    log = logging.getLogger("jsonrpc.TCPListener")

    def __init__(self, reactor, address, acceptHandler):
        self._address = address
        self.sock = sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(address)
        sock.listen(10)
        self._acceptHandler = acceptHandler
        self._reactor = reactor

    def _accept(self):
        sock, addr = self.sock.accept()

        client = TCPClient(self._reactor, sock)
        try:
            self._acceptHandler(self, client)
        except:
            self.log.warning("Accept handler threw an unexpected exception",
                             exc_info=True)

        return client

    def fileno(self):
        return self.sock.fileno()

    def close(self):
        self.sock.close()


class TCPReactor(object):
    log = logging.getLogger("jsonrpc.TCPReactor")

    def __init__(self):
        self._inputEvent = utils.PollEvent()
        # TODO: Close on exec
        self._trackedObjects = set()
        self._isRunning = False

    def createListener(self, address, acceptHandler):
        l = TCPListener(self, address, acceptHandler)

        self._trackedObjects.add(l)
        self._inputEvent.set()
        return l

    def wakeup(self):
        self._inputEvent.set()

    def process_requests(self):
        poller = poll()
        self.log.debug("Starting to accept clients")

        objMap = {}
        poller.register(self._inputEvent, POLLIN | POLLPRI)
        # TODO: Exist condition
        while True:
            for obj in self._trackedObjects:
                try:
                    fd = obj.fileno()
                except:
                    continue

                if fd not in objMap:
                    objMap[fd] = obj
                    poller.register(fd, POLLIN | POLLPRI)

            for fd, obj in objMap.iteritems():
                if not isinstance(obj, TCPClient):
                    continue

                if obj._hasSendData():
                    poller.modify(fd, (POLLIN | POLLPRI | POLLOUT))
                else:
                    poller.modify(fd, (POLLIN | POLLPRI))

            for fd, ev in poller.poll():
                if fd == self._inputEvent.fileno():
                    self._inputEvent.clear()
                    continue

                obj = objMap[fd]
                if ev & (POLLERR | POLLHUP):
                    if isinstance(obj, TCPListener):
                        self.log.info("Listening socket closed")
                    else:
                        self.log.debug("Connection closed")

                    self._trackedObjects.discard(obj)
                    del objMap[fd]
                    poller.unregister(fd)

                elif isinstance(obj, TCPListener):
                    try:
                        client = obj._accept()
                    except (OSError, IOError):
                        continue

                    self.log.debug("Processing new connection")
                    self._trackedObjects.add(client)
                else:  # TCPClient
                    try:
                        if ev & (POLLIN | POLLPRI):
                            obj._processInput()

                        if ev & POLLOUT:
                            obj._processOutput()
                    except:
                        poller.unregister(fd)
                        obj.close()
                        self._trackedObjects.discard(obj)
                        del objMap[fd]

    def stop(self):
        for obj in self._trackedObjects:
            obj.close()
