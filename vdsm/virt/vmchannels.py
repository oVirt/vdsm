#
# Copyright 2014 Red Hat, Inc.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#

import errno
import threading
import time
import select
import logging

from vdsm.utils import NoIntrPoll

# How many times a reconnect should be performed before a cooldown will be
# applied
COOLDOWN_RECONNECT_THRESHOLD = 5


class Listener(threading.Thread):
    """
    An events driven listener which handle messages from virtual machines.
    """
    def __init__(self, log):
        threading.Thread.__init__(self, name='VM Channels Listener')
        self.daemon = True
        self.log = log
        self._quit = False
        self._epoll = select.epoll()
        self._channels = {}
        self._unconnected = {}
        self._update_lock = threading.Lock()
        self._add_channels = {}
        self._del_channels = []
        self._timeout = None

    def _unregister_fd(self, fileno):
        try:
            self._epoll.unregister(fileno)
        except IOError as e:
            if e.errno != errno.ENOENT:
                raise
            # This case shouldn't happen anymore - But let's track it anyway
            self.log.debug("Failed to unregister FD from epoll (ENOENT): %d",
                           fileno)

    def _handle_event(self, fileno, event):
        """ Handle an epoll event occurred on a specific file descriptor. """
        reconnect = False
        if (event & (select.EPOLLHUP | select.EPOLLERR)):
            self.log.debug("Received %.08X on fileno %d", event, fileno)
            if fileno in self._channels:
                reconnect = True
            else:
                self.log.warning('Received an error on an untracked fd(%d)',
                                 fileno)
        elif (event & select.EPOLLIN):
            obj = self._channels.get(fileno, None)
            if obj:
                obj['timeout_seen'] = False
                obj['reconnects'] = 0
                try:
                    if obj['read_cb'](obj['opaque']):
                        obj['read_time'] = time.time()
                    else:
                        reconnect = True
                except:
                    self.log.exception("Exception on read callback.")
            else:
                self.log.debug("Received epoll event %.08X for no longer "
                               "tracked fd = %d", event, fileno)

        if reconnect:
            self._prepare_reconnect(fileno)

    def _prepare_reconnect(self, fileno):
            # fileno will be closed by create_cb
            self._unregister_fd(fileno)
            obj = self._channels.pop(fileno)
            obj['timeout_seen'] = False
            try:
                fileno = obj['create_cb'](obj['opaque'])
            except:
                self.log.exception("An error occurred in the create callback "
                                   "fileno: %d.", fileno)
            else:
                with self._update_lock:
                    self._unconnected[fileno] = obj

    def _handle_timeouts(self):
        """
        Scan channels and notify registered client if a timeout occurred on
        their file descriptor.
        """
        now = time.time()
        for (fileno, obj) in self._channels.items():
            if (now - obj['read_time']) >= self._timeout:
                if not obj.get('timeout_seen', False):
                    self.log.debug("Timeout on fileno %d.", fileno)
                    obj['timeout_seen'] = True
                try:
                    obj['timeout_cb'](obj['opaque'])
                    obj['read_time'] = now
                except:
                    self.log.exception("Exception on timeout callback.")

    def _do_add_channels(self):
        """ Add new channels to unconnected channels list. """
        for (fileno, obj) in self._add_channels.items():
            self.log.debug("fileno %d was added to unconnected channels.",
                           fileno)
            self._unconnected[fileno] = obj
        self._add_channels.clear()

    def _do_del_channels(self):
        """ Remove requested channels from listener. """
        for fileno in self._del_channels:
            self._add_channels.pop(fileno, None)
            self._unconnected.pop(fileno, None)
            self._channels.pop(fileno, None)
            self.log.debug("fileno %d was removed from listener.", fileno)
        self._del_channels = []

    def _update_channels(self):
        """ Update channels list. """
        with self._update_lock:
            self._do_add_channels()
            self._do_del_channels()

    def _handle_unconnected(self):
        """
        Scan the unconnected channels and give the registered client a chance
        to connect their channel.
        """
        now = time.time()
        for (fileno, obj) in self._unconnected.items():
            if obj.get('cooldown'):
                if (now - obj['cooldown_time']) >= self._timeout:
                    obj['cooldown'] = False
                    self.log.log(logging.TRACE, "Reconnect attempt fileno "
                                 "%d", fileno)
                else:
                    continue

            try:
                success = obj['connect_cb'](obj['opaque'])
            except:
                self.log.exception("Exception on connect callback.")
            else:
                if success:
                    self.log.debug("Connecting to fileno %d succeeded.",
                                   fileno)
                    del self._unconnected[fileno]
                    self._channels[fileno] = obj
                    obj['read_time'] = time.time()
                    self._epoll.register(fileno, select.EPOLLIN)
                else:
                    obj['reconnects'] = obj.get('reconnects', 0) + 1
                    if obj['reconnects'] >= COOLDOWN_RECONNECT_THRESHOLD:
                        obj['cooldown_time'] = time.time()
                        obj['cooldown'] = True
                        self.log.log(logging.TRACE, "fileno %d was moved into "
                                     "cooldown", fileno)

    def _wait_for_events(self):
        """ Wait for an epoll event and handle channels' timeout. """
        events = NoIntrPoll(self._epoll.poll, 1)
        for (fileno, event) in events:
            self._handle_event(fileno, event)
        else:
            self._update_channels()
            if (self._timeout is not None) and (self._timeout > 0):
                self._handle_timeouts()
            with self._update_lock:
                self._handle_unconnected()

    def run(self):
        """ The listener thread's function. """
        self.log.debug("Starting VM channels listener thread.")
        self._quit = False
        try:
            while not self._quit:
                self._wait_for_events()
        except:
            self.log.exception("Unhandled exception caught in vm channels "
                               "listener thread")
        finally:
            self.log.debug("VM channels listener thread has ended.")

    def stop(self):
        """" Stop the listener execution. """
        self._quit = True
        self.log.debug("VM channels listener was stopped.")

    def settimeout(self, seconds):
        """ Set the timeout value (in seconds) for all channels. """
        self.log.info("Setting channels' timeout to %d seconds.", seconds)
        self._timeout = seconds

    def register(self, create_callback, connect_callback, read_callback,
                 timeout_callback, opaque):
        """ Register a new file descriptor to the listener. """
        fileno = create_callback(opaque)
        self.log.debug("Add fileno %d to listener's channels.", fileno)
        with self._update_lock:
            self._add_channels[fileno] = {
                'connect_cb': connect_callback,
                'read_cb': read_callback, 'timeout_cb': timeout_callback,
                'opaque': opaque, 'create_cb': create_callback,
                'read_time': 0.0,
            }

    def unregister(self, fileno):
        """ Unregister an exist file descriptor from the listener. """
        self.log.debug("Delete fileno %d from listener.", fileno)
        with self._update_lock:
            # Threadsafe, fileno will be closed by caller
            # NOTE: unregister_fd has to be called here, otherwise it'd be
            # removed from epoll after the socket has been closed, which is
            # incorrect
            # NOTE: unregister_fd must be only called if fileno is not in
            # _add_channels and not in _unconnected because it might be
            # about to reconnect after an error or has just been added.
            # In those cases fileno is not being tracked by epoll and
            # would result in an ENOENT error
            if (fileno not in self._add_channels and
                    fileno not in self._unconnected):
                self._unregister_fd(fileno)
            self._del_channels.append(fileno)
