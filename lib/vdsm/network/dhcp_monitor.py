# Copyright 2020 Red Hat, Inc.
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

from contextlib import contextmanager
import os
import json
import logging
import socketserver
import threading

from vdsm.common.constants import P_VDSM_RUN

SOCKET_DEFAULT = os.path.join(P_VDSM_RUN, 'dhcp-monitor.sock')

_monitor_instance = None
_monitor_lock = threading.Lock()

_monitored_item_pool_instance = None
_monitored_item_pool_lock = threading.Lock()


class MonitoredItemPool(object):
    """
    Thread safe singleton for keeping track which interfaces are monitored
    (Methods are not thread safe)
    """

    def __init__(self):
        self._item_pool = set()

    @staticmethod
    def instance():
        global _monitored_item_pool_instance
        if _monitored_item_pool_instance is None:
            with _monitored_item_pool_lock:
                if _monitored_item_pool_instance is None:
                    _monitored_item_pool_instance = MonitoredItemPool()
        return _monitored_item_pool_instance

    def add(self, item):
        self._item_pool.add(item)

    def remove(self, item):
        self._item_pool.remove(item)

    def is_item_in_pool(self, item):
        return item in self._item_pool

    def is_pool_empty(self):
        return len(self._item_pool) == 0


class Monitor(object):
    """
    Monitor that creates UNIX socket and handles the event notification
    """

    def __init__(self, socket_path=SOCKET_DEFAULT):
        self._socket = socket_path
        self._handlers = []
        self._server = socketserver.UnixStreamServer(
            socket_path, _MonitorHandler
        )
        self._thread = threading.Thread(
            target=self._server.serve_forever, name='dhcp-monitor'
        )
        self._netapi = None
        self._cif = None

        self._thread.setDaemon(True)

    @staticmethod
    def instance(**kwargs):
        global _monitor_instance
        if _monitor_instance is None:
            with _monitor_lock:
                if _monitor_instance is None:
                    _monitor_instance = Monitor(**kwargs)
        return _monitor_instance

    def start(self):
        logging.info('Starting DHCP monitor.')
        self._thread.start()

    def stop(self):
        logging.info('Stopping DHCP monitor.')
        self._server.shutdown()
        self._thread.join()
        try:
            os.remove(self._socket)
        except OSError:
            logging.warning('DHCP monitor socket cannot be removed.')

    def add_handler(self, handler):
        self._handlers.append(handler)

    def handle_event(self, event):
        for handler in self._handlers:
            handler(event)


class ResponseField(object):
    ACTION = 'action'
    IPADDR = 'ip'
    IPMASK = 'mask'
    IPROUTE = 'route'
    IFACE = 'iface'
    FAMILY = 'family'


class _MonitorHandler(socketserver.BaseRequestHandler):
    def handle(self):
        content = self.request.recv(2048).strip()
        Monitor.instance().handle_event(json.loads(content))


def initialize_monitor(cif, netapi):
    global _monitor_instance
    try:
        monitor = Monitor.instance()
        monitor.start()
    except Exception as e:
        _monitor_instance = None
        raise e


def clear_monitor():
    global _monitor_instance
    Monitor.instance().stop()
    _monitor_instance = None


@contextmanager
def initialize_monitor_ctx(cif, netapi):
    initialize_monitor(cif, netapi)
    try:
        yield
    finally:
        clear_monitor()
