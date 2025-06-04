# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-FileCopyrightText: 2014-2017 Saggi Mizrahi
# SPDX-License-Identifier: GPL-2.0-or-later

# Asyncore uses inheritance all around which makes it not flexible enough for
# us to use. This does tries to reuse enough code from the original asyncore
# while enabling compositing instead of inheritance.
from __future__ import absolute_import
from __future__ import division

import asyncore
import errno
import logging
import socket
import ssl

from vdsm import sslutils
from vdsm.common.eventfd import EventFD


_BLOCKING_IO_ERRORS = (errno.EAGAIN, errno.EALREADY, errno.EINPROGRESS,
                       errno.EWOULDBLOCK)


class Dispatcher(asyncore.dispatcher):

    _log = logging.getLogger("vds.dispatcher")

    def __init__(self, impl=None, sock=None, map=None):
        # This has to be done before the super initialization because
        # dispatcher implements __getattr__.
        self.__impl = None
        asyncore.dispatcher.__init__(self, sock=sock, map=map)
        if impl is not None:
            self.switch_implementation(impl)
        self._log.debug("Initialized dispatcher %s", self)

    def handle_connect(self):
        self._delegate_call("handle_connect")

    def handle_close(self):
        try:
            self._delegate_call("handle_close")
        finally:
            self.close()

    def handle_accept(self):
        self._delegate_call("handle_accept")

    def handle_expt(self):
        self._delegate_call("handle_expt")

    def handle_error(self):
        self._delegate_call("handle_error")

    def readable(self):
        return self._delegate_call("readable")

    def writable(self):
        return self._delegate_call("writable")

    def handle_read(self):
        self._delegate_call("handle_read")

    def handle_write(self):
        self._delegate_call("handle_write")

    def close(self):
        if self.closing:
            return
        self._log.debug("Closing dispatcher %s", self)
        self.closing = True
        asyncore.dispatcher.close(self)

    def switch_implementation(self, impl):
        self.__impl = impl

        if hasattr(impl, 'init'):
            impl.init(self)

    def next_check_interval(self):
        """
        Return the relative timeout wanted between poller refresh checks

        The function should return the number of seconds it wishes to wait
        until the next update. None should be returned in cases where the
        implementation doesn't care.

        Note that this value is a recommendation only.
        """
        default_func = lambda: None
        return getattr(self.__impl, "next_check_interval", default_func)()

    def set_heartbeat(self, outgoing, incoming):
        if self.__impl and hasattr(self.__impl, 'setHeartBeat'):
            self.__impl.setHeartBeat(outgoing, incoming)

    def create_socket(self, addr, sslctx=None, family=socket.AF_UNSPEC,
                      type=socket.SOCK_STREAM):
        addrinfo = socket.getaddrinfo(addr[0], addr[1], family, type)
        self.family_and_type = family, type
        family, socktype, proto, _, sockaddr = addrinfo[0]
        sock = socket.socket(family, socktype, proto)
        if sslctx:
            sock = sslctx.wrapSocket(sock)
        sock.setblocking(0)
        self.set_socket(sock)

    def recv(self, buffer_size):
        try:
            data = self.socket.recv(buffer_size)
            if data == b'':
                # a closed connection is indicated by signaling
                # a read condition, and having recv() return 0.
                self.handle_close()
                return b''
            else:
                return data
        except sslutils.SSLError as e:
            if e.errno == ssl.SSL_ERROR_WANT_READ:
                return None
            self._log.debug('SSL error receiving from %s: %s', self, e)
            self.handle_close()
            return b''
        except socket.error as why:
            # winsock sometimes raises ENOTCONN
            # according to asyncore.dispatcher#recv docstring
            # we need additional errnos.
            if why.args[0] in _BLOCKING_IO_ERRORS:
                return None
            elif why.args[0] in asyncore._DISCONNECTED:
                self.handle_close()
                return b''
            else:
                raise

    def send(self, data):
        try:
            result = self.socket.send(data)
            if result == -1:
                return 0
            return result
        except sslutils.SSLError as e:
            if e.errno == ssl.SSL_ERROR_WANT_WRITE:
                return 0
            self._log.debug('SSL error sending to %s: %s ', self, e)
            self.handle_close()
            return 0
        except socket.error as why:
            if why.args[0] in _BLOCKING_IO_ERRORS:
                return 0
            elif why.args[0] in asyncore._DISCONNECTED:
                self.handle_close()
                return 0
            else:
                raise

    def del_channel(self, map=None):
        asyncore.dispatcher.del_channel(self, map)
        self.__impl = None
        self.connected = False

    def _delegate_call(self, name):
        if hasattr(self.__impl, name):
            return getattr(self.__impl, name)(self)
        else:
            return getattr(asyncore.dispatcher, name)(self)

    # Override asyncore.dispatcher logging to use our logger
    log = _log.debug

    def log_info(self, message, type='info'):
        level = getattr(logging, type.upper(), None)
        if not isinstance(level, int):
            raise ValueError('Invalid log level: %s' % type)
        self._log.log(level, message)


class AsyncoreEvent(asyncore.file_dispatcher):
    def __init__(self, map=None):
        self.closing = False
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
        if self.closing:
            return
        self.closing = True
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

    def create_dispatcher(self, sock, impl=None):
        return Dispatcher(impl=impl, sock=sock, map=self._map)

    def process_requests(self):
        self._is_running = True
        while self._is_running:
            asyncore.loop(
                timeout=self._get_timeout(self._map),
                use_poll=True,
                map=self._map,
                count=1,
            )

        for dispatcher in list(self._map.values()):
            dispatcher.close()

        self._map.clear()

    def _get_timeout(self, map):
        timeout = 30.0
        for disp in list(self._map.values()):
            if hasattr(disp, "next_check_interval"):
                interval = disp.next_check_interval()
                if interval is not None and interval >= 0:
                    timeout = min(interval, timeout)
        return timeout

    def wakeup(self):
        self._wakeupEvent.set()

    def stop(self):
        self._is_running = False
        try:
            self.wakeup()
        except (IOError, OSError):
            # Client woke up and closed the event dispatcher without our help
            pass

    def reconnect(self, address, sslctx, impl):
        dispatcher = self.create_dispatcher(None, impl)
        dispatcher.create_socket(address, sslctx)
        dispatcher.connect(address)

        return dispatcher
