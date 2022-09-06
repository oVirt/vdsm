# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

import logging
import threading

from vdsm.common import concurrent
from vdsm.network.netlink import monitor

_monitor_instance = None
_monitor_lock = threading.Lock()


class Monitor(object):
    def __init__(self):
        self._handlers = []
        self._nl_monitor = monitor.ifla_monitor(groups=('link',))
        self._thread = concurrent.thread(
            self.serve_forever, name='bond-monitor'
        )

    @staticmethod
    def instance():
        global _monitor_instance
        if _monitor_instance is None:
            with _monitor_lock:
                if _monitor_instance is None:
                    _monitor_instance = Monitor()
        return _monitor_instance

    def start(self):
        logging.info('Starting Bond monitor.')
        self._nl_monitor.start()
        self._thread.start()

    def stop(self):
        logging.info('Stopping Bond monitor.')
        self._nl_monitor.stop()
        self._nl_monitor.wait()
        self._thread.join()

    def add_handler(self, handler):
        self._handlers.append(handler)

    def handle_event(self, event):
        for handler in self._handlers:
            handler(event)

    def serve_forever(self):
        for event in self._nl_monitor:
            self.handle_event(event)


def initialize_monitor(cif):
    def notify_engine(event):
        if event.get('IFLA_EVENT') == 'IFLA_EVENT_BONDING_FAILOVER':
            cif.notify('|net|host_conn|no_id')

    global _monitor_instance
    try:
        monitor = Monitor.instance()
        monitor.add_handler(notify_engine)
        monitor.start()
    except Exception as e:
        _monitor_instance = None
        raise e


def stop():
    Monitor.instance().stop()
