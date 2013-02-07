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

import struct
import asyncore
import asynchat
import socket
import os
import threading
import logging

_Size = struct.Struct("!Q")

_STATE_LEN = "Waiting for message length"
_STATE_MSG = "Waiting for message"


class AsyncoreClient(asynchat.async_chat):
    log = logging.getLogger("jsonrpc.AsyncoreClient")

    def __init__(self, sock, reactor, addr):
        asynchat.async_chat.__init__(self, sock=sock, map=reactor._map)
        if sock is None:
            self.create_socket(socket.AF_INET, socket.SOCK_STREAM)

        self._addr = addr
        self._ibuff = []
        self.set_terminator(_Size.size)
        self._mlen = _Size.size
        self._state = _STATE_LEN
        self._inbox = None
        self._reactor = reactor
        self._sending = threading.Lock()

    def initiate_send(self):
        # So we can push data from outside the asyncore loop
        self._sending.acquire()
        asynchat.async_chat.initiate_send(self)
        self._sending.release()

    def collect_incoming_data(self, data):
        self._ibuff.append(data)

    def set_inbox(self, inbox):
        self._inbox = inbox

    def found_terminator(self):
        ibuff = ''.join(self._ibuff)

        l = self._mlen
        if len(ibuff) > l:
            self._ibuff = [ibuff[l:]]
            ibuff = ibuff[:l]
        else:
            self._ibuff = []

        if self._state == _STATE_LEN:
            mlen = _Size.unpack(ibuff)[0]
            self.log.debug("Got request to recv %d bytes", mlen)
            self._state = _STATE_MSG
            self.set_terminator(mlen)
            self._mlen = mlen

        elif self._state == _STATE_MSG:
            if self._inbox is not None:
                self.log.debug("Pushing message to inbox")
                self._inbox.put((AsyncoreClientWrapper(self), ibuff))

            self.set_terminator(_Size.size)
            self._mlen = _Size.size
            self._state = _STATE_LEN

    def sendMessage(self, data):
        data = _Size.pack(len(data)) + data
        self.push(data)
        self._reactor.wakeup()


# Because asyncore client objects implements a lot methods that we might want
# to use for different things (eg. send) we need a wrapper so that inherited
# interfaces are not exposed and cause trouble.
class AsyncoreClientWrapper(object):
    def __init__(self, client):
        self._client = client

    def connect(self):
        self._client.connect(self._client._addr)

    def setInbox(self, inbox):
        self._client.set_inbox(inbox)

    def send(self, message):
        self._client.sendMessage(message)

    def close(self):
        self._client.close()


# FIXME: We should go about making a listener wrapper like the client wrapper
#        This is not as high priority as users don't interact with listeners
#        as much
class AsyncoreListener(asyncore.dispatcher):
    log = logging.getLogger("jsonrpc.AsyncoreListener")

    def __init__(self, reactor, address, acceptHandler):
        self._reactor = reactor
        asyncore.dispatcher.__init__(self, map=reactor._map)
        self.create_socket(socket.AF_INET, socket.SOCK_STREAM)
        self.set_reuse_addr()
        self.bind(address)
        self.listen(5)

        self._acceptHandler = acceptHandler

    def handle_accept(self):
        pair = self.accept()
        if pair is None:
            return

        sock, addr = pair
        self.log.debug("Accepting connection from client "
                       "at tcp://%s:%s", addr[0], addr[1])

        client = AsyncoreClient(sock, self._reactor, addr)
        clientWrapper = AsyncoreClientWrapper(client)
        self._acceptHandler(self, clientWrapper)

    def writable(self):
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


class AsyncoreReactor(object):
    def __init__(self):
        self._map = {}
        self._isRunning = False
        self._wakeupEvent = _AsyncoreEvent(self._map)

    def createListener(self, address, acceptHandler):
        l = AsyncoreListener(self, address, acceptHandler)
        self.wakeup()
        return l

    def createClient(self, address):
        client = AsyncoreClient(None, self, address)
        return AsyncoreClientWrapper(client)

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
