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
from sys import py3kwarning
from warnings import filterwarnings, catch_warnings
from threading import Lock

from collections import deque


# This is a copy of the standard library asyncore converted to support
# compositing. Also fixes races in original implementation.
class AsyncChat(object):
    # these are overridable defaults

    ac_in_buffer_size = 4096
    ac_out_buffer_size = 4096

    def __init__(self, impl):
        self._fifoLock = Lock()
        self._impl = impl

        self.ac_in_buffer = ''

        self.incoming = []

        # we toss the use of the "simple producer" and replace it with
        # a pure deque, which the original fifo was a wrapping of
        self.producer_fifo = deque()

    def init(self, dispatcher):
        self._impl.init(self)

    def collect_incoming_data(self, data):
        self._impl.collect_incoming_data(data, self)

    def _collect_incoming_data(self, data):
        self.incoming.append(data)

    def _get_data(self):
        d = ''.join(self.incoming)
        del self.incoming[:]
        return d

    def found_terminator(self):
        self._impl.found_terminator(self)

    def set_terminator(self, term):
        """Set the input delimiter.  Can be a fixed string of any length, an
        integer, or None"""
        self.terminator = term

    def get_terminator(self):
        return self.terminator

    # grab some more data from the socket,
    # throw it to the collector method,
    # check for the terminator,
    # if found, transition to the next state.
    def handle_read(self, dispatcher):

        try:
            data = dispatcher.recv(self.ac_in_buffer_size)
            if data is None:
                return
        except socket.error:
            dispatcher.handle_error()
            return

        self.ac_in_buffer = self.ac_in_buffer + data

        # Continue to search for self.terminator in self.ac_in_buffer,
        # while calling self.collect_incoming_data.  The while loop
        # is necessary because we might read several data+terminator
        # combos with a single recv(4096).

        while self.ac_in_buffer:
            lb = len(self.ac_in_buffer)
            terminator = self.get_terminator()
            if not terminator:
                # no terminator, collect it all
                self.collect_incoming_data(self.ac_in_buffer)
                self.ac_in_buffer = ''
            elif isinstance(terminator, (long, int)):
                # numeric terminator
                n = terminator
                if lb < n:
                    self.collect_incoming_data(self.ac_in_buffer)
                    self.ac_in_buffer = ''
                    self.terminator = self.terminator - lb
                else:
                    self.collect_incoming_data(self.ac_in_buffer[:n])
                    self.ac_in_buffer = self.ac_in_buffer[n:]
                    self.terminator = 0
                    self.found_terminator()
            else:
                # 3 cases:
                # 1) end of buffer matches terminator exactly:
                #    collect data, transition
                # 2) end of buffer matches some prefix:
                #    collect data to the prefix
                # 3) end of buffer does not match any prefix:
                #    collect data
                terminator_len = len(terminator)
                index = self.ac_in_buffer.find(terminator)
                if index != -1:
                    # we found the terminator
                    if index > 0:
                        # don't bother reporting the empty string(source of
                        # subtle bugs)
                        self.collect_incoming_data(self.ac_in_buffer[:index])
                    self.ac_in_buffer = \
                        self.ac_in_buffer[index + terminator_len:]
                    # This does the Right Thing if the terminator is changed
                    # here.
                    self.found_terminator()
                else:
                    # check for a prefix of the terminator
                    index = self._find_prefix_at_end(self.ac_in_buffer,
                                                     terminator)
                    if index != 0:
                        if index != lb:
                            # we found a prefix, collect up to the prefix
                            self.collect_incoming_data(
                                self.ac_in_buffer[:-index])
                            self.ac_in_buffer = self.ac_in_buffer[-index:]
                        break
                    else:
                        # no prefix, collect it all
                        self.collect_incoming_data(self.ac_in_buffer)
                        self.ac_in_buffer = ''

    def _find_prefix_at_end(self, haystack, needle):
        l = len(needle) - 1
        while l and not haystack.endswith(needle[:l]):
            l -= 1
        return l

    def handle_write(self, dispatcher):
        self.initiate_send(dispatcher)

    def handle_close(self, dispatcher):
        dispatcher.close()

    def push(self, data, dispatcher):
        with self._fifoLock:
            sabs = self.ac_out_buffer_size
            if len(data) > sabs:
                for i in xrange(0, len(data), sabs):
                    self.producer_fifo.append(data[i:i+sabs])
            else:
                self.producer_fifo.append(data)

    def push_with_producer(self, producer, dispatcher):
        with self._fifoLock:
            self.producer_fifo.append(producer)

    def readable(self, dispatcher):
        "predicate for inclusion in the readable for select()"
        return 1

    def writable(self, dispatcher):
        "predicate for inclusion in the writable for select()"
        with self._fifoLock:
            return self.producer_fifo or(not dispatcher.connected)

    def close_when_done(self):
        "automatically close this channel once the outgoing queue is empty"
        with self._fifoLock:
            self.producer_fifo.append(None)

    def initiate_send(self, dispatcher):
        while self.producer_fifo and dispatcher.connected:
            with self._fifoLock:
                first = self.producer_fifo[0]
                # handle empty string/buffer or None entry
                if not first:
                    del self.producer_fifo[0]
                    if first is None:
                        dispatcher.handle_close()
                        return

                # handle classic producer behavior
                obs = self.ac_out_buffer_size
                try:
                    with catch_warnings():
                        if py3kwarning:
                            filterwarnings("ignore", ".*buffer",
                                           DeprecationWarning)
                        data = buffer(first, 0, obs)
                except TypeError:
                    data = first.more()
                    if data:
                        self.producer_fifo.appendleft(data)
                    else:
                        del self.producer_fifo[0]
                    continue

            # send the data
            try:
                num_sent = dispatcher.send(data)
            except socket.error:
                dispatcher.handle_error()
                return

            with self._fifoLock:
                if num_sent:
                    if num_sent < len(data) or obs < len(first):
                        self.producer_fifo[0] = first[num_sent:]
                    else:
                        del self.producer_fifo[0]
                # we tried to send some actual data
                return

    def discard_buffers(self):
        # Emergencies only!
        self.ac_in_buffer = ''
        del self.incoming[:]
        with self._fifoLock:
            self.producer_fifo.clear()


class Dispatcher(asyncore.dispatcher):
    def __init__(self, impl, sock=None, map=None):
        self.__impl = impl
        asyncore.dispatcher.__init__(self, sock=sock, map=map)

        try:
            impl.init(self)
        except AttributeError:
            # impl.init() is optional.
            pass

    def __invoke(self, name, *args, **kwargs):
        if hasattr(self.__impl, name):
            return getattr(self.__impl, name)(self, *args, **kwargs)
        else:
            return getattr(asyncore.dispatcher, name)(self, *args, **kwargs)

    def handle_connect(self):
        return self.__invoke("handle_connect")

    def handle_close(self):
        return self.__invoke("handle_close")

    def handle_accept(self):
        return self.__invoke("handle_accept")

    def handle_expt(self):
        return self.__invoke("handle_expt")

    def handle_error(self):
        return self.__invoke("handle_error")

    def readable(self):
        return self.__invoke("readable")

    def writable(self):
        return self.__invoke("writable")

    def handle_read(self):
        return self.__invoke("handle_read")

    def handle_write(self):
        return self.__invoke("handle_write")

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
