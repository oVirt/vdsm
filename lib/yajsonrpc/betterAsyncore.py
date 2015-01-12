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

# Asyncore uses inheritance all around which makes it not flexible enought for
# us to use. This does tries to reuse enought code from the original asyncore
# while enabling compositing instead of inheritance.
import asyncore
import socket
import types
from errno import EWOULDBLOCK

from vdsm.infra.eventfd import EventFD


class Dispatcher(asyncore.dispatcher):
    def __init__(self, impl, sock=None, map=None):
        self.__impl = impl
        asyncore.dispatcher.__init__(self, sock=sock, map=map)

        try:
            impl.init(self)
        except AttributeError:
            # impl.init() is optional.
            pass

        self._bind_implementation()

    def _bind_implementation(self):
        for attr_name in (
            "handle_accept",
            "handle_close",
            "handle_connect",
            "handle_error",
            "handle_expt",
            "handle_read",
            "handle_write",
            "readable",
            "writable",
        ):
            method = getattr(
                self.__impl,
                attr_name,
                getattr(
                    asyncore.dispatcher,
                    attr_name
                )
            )

            setattr(
                self,
                attr_name,
                types.MethodType(
                    method,
                    self,
                    Dispatcher,
                )
            )

    def recv(self, buffer_size):
        try:
            data = self.socket.recv(buffer_size)
            if data == "":
                # a closed connection is indicated by signaling
                # a read condition, and having recv() return 0.
                self.handle_close()
                return ''
            else:
                return data
        except socket.error, why:
            # winsock sometimes raises ENOTCONN
            if why.args[0] in asyncore._DISCONNECTED:
                self.handle_close()
                return ''
            else:
                raise

    def send(self, data):
        try:
            result = self.socket.send(data)
            if result == -1:
                return 0
            return result
        except socket.error, why:
            if why.args[0] == EWOULDBLOCK:
                return 0
            elif why.args[0] in asyncore._DISCONNECTED:
                self.handle_close()
                return 0
            else:
                raise


class AsyncoreEvent(asyncore.file_dispatcher):
    def __init__(self, map=None):
        self._eventfd = EventFD()
        try:
            asyncore.file_dispatcher.__init__(
                self,
                self._eventfd.fileno(),
                map=map
            )
        except:
            self._eventfd.close()
            raise

    def writable(self):
        return False

    def set(self):
        self._eventfd.write(1)

    def handle_read(self):
        self._eventfd.read()

    def close(self):
        try:
            self._eventfd.close()
        except (OSError, IOError):
            pass

        asyncore.file_dispatcher.close(self)


class Reactor(object):
    """
    map dictionary maps sock.fileno() to channels to watch. We add channels to
    it by running add_dispatcher and removing by remove_dispatcher.
    It is used by asyncore loop to know which channels events to track.

    We use eventfd as mechanism to trigger processing when needed.
    """

    def __init__(self):
        self._map = {}
        self._is_running = False
        self._wakeupEvent = AsyncoreEvent(self._map)

    def add_dispatcher(self, disp):
        disp.add_channel(self._map)

    def remove_dispatcher(self, disp):
        disp.del_channel(self._map)

    def process_requests(self):
        self._is_running = True
        while self._is_running:
            asyncore.loop(timeout=.5, use_poll=True, map=self._map, count=1)

        for dispatcher in self._map.values():
            dispatcher.close()

        self._map.clear()

    def wakeup(self):
        self._wakeupEvent.set()

    def stop(self):
        self._is_running = False
        try:
            self.wakeup()
        except (IOError, OSError):
            # Client woke up and closed the event dispatcher without our help
            pass
