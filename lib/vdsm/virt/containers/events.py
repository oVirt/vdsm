#
# Copyright 2015-2016 Red Hat, Inc.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License as published
# by the Free Software Foundation; either version 2 of the License, or
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

"""
Event handling emulation. We need to emulate only the very basic lifecycle
events, but we need to support callbacks.
"""


from __future__ import absolute_import
from __future__ import division

import collections
import logging
import threading


_Callback = collections.namedtuple('_Callback',
                                   ['conn', 'dom', 'func', 'opaque'])


class Handler(object):

    _log = logging.getLogger('virt.containers.event')

    def __init__(self, name=None, parent=None):
        self._name = id(self) if name is None else name
        self._parent = parent
        self._lock = threading.Lock()
        self.events = collections.defaultdict(list)

    def register(self, event_id, conn, dom, func, opaque=None):
        # TODO: weakrefs?
        cb = _Callback(conn, dom, func, opaque)
        # TODO: debug?
        self._log.debug('[%s] %i -> %s', self._name, event_id, cb)
        with self._lock:
            self.events[event_id].append(cb)

    def fire(self, event_id, dom, *args):
        cbs = self.get_callbacks(event_id)
        if cbs is None:
            self._log.warning('[%s] unhandled event %r', self._name, event_id)
            return

        for cb in cbs:
            arguments = list(args)
            if cb.opaque is not None:
                arguments.append(cb.opaque)
            domain = cb.dom if dom is None else dom
            self._log.debug('firing: %s(%s, %s, %s)',
                            cb.func, cb.conn, domain, arguments)
            return cb.func(cb.conn, domain, *arguments)

    def get_callbacks(self, event_id):
        with self._lock:
            callback = self.events.get(event_id, None)
        if callback is not None:
            return callback
        if self._parent is not None:
            self._log.debug('[%s] unknown event %r',
                            self._name, event_id)
            return self._parent.get_callbacks(event_id)
        return None

    @property
    def registered(self):
        with self._lock:
            return tuple(self.events.keys())


root = Handler(name='root')


def fire(event_id, dom, *args):
    root.fire(event_id, dom, *args)
