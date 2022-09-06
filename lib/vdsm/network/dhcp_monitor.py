# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from contextlib import contextmanager
import logging
import threading

from vdsm.common import concurrent
from vdsm.network.netlink import monitor
from vdsm.network.ip.address import IPAddressData


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

    def clear_pool(self):
        self._item_pool.clear()


class Monitor(object):
    """
    Monitor that handles new ip notifications
    """

    def __init__(self):
        self._handlers = []
        self._nl_monitor = monitor.object_monitor(
            groups=('ipv4-ifaddr', 'ipv6-ifaddr')
        )
        self._thread = concurrent.thread(
            self.serve_forever, name='dhcp-monitor'
        )

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
        self._nl_monitor.start()
        self._thread.start()

    def stop(self):
        logging.info('Stopping DHCP monitor.')
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


class EventField(object):
    class Scope(object):
        KEY = 'scope'
        GLOBAL = 'global'

    class Event(object):
        KEY = 'event'
        NEW_ADDR = 'new_addr'

    class Family(object):
        KEY = 'family'
        IPV4 = 'inet'
        IPV6 = 'inet6'

    ADDRESS = 'address'
    IFACE = 'label'


def initialize_monitor(cif, net_api):
    global _monitor_instance
    try:
        monitor = Monitor.instance()
        monitor.add_handler(
            lambda event: _dhcp_event_handler(cif, net_api, event)
        )
        monitor.start()
    except Exception as e:
        _monitor_instance = None
        raise e


def clear_monitor():
    global _monitor_instance
    Monitor.instance().stop()
    _monitor_instance = None


@contextmanager
def initialize_monitor_ctx(cif, net_api):
    initialize_monitor(cif, net_api)
    try:
        yield
    finally:
        clear_monitor()


def _dhcp_event_handler(cif, net_api, event):
    if not _is_valid_event(event):
        return

    iface = event[EventField.IFACE]
    family = _get_event_family(event.get(EventField.Family.KEY))
    address = IPAddressData(event[EventField.ADDRESS], iface)

    if not net_api.is_dhcp_ip_monitored(iface, family):
        logging.debug(
            'Nic %s is not configured for IPv%s monitoring.', iface, family
        )
        return

    if family == 4:
        net_api.add_dynamic_source_route_rules(
            iface, address.address, address.prefixlen
        )

    cif.notify('|net|host_conn|no_id')
    net_api.remove_dhcp_monitoring(iface, family)


def _is_valid_event(event):
    # Skipping local addresses, removal of address or empty address field
    return (
        event.get(EventField.Scope.KEY) == EventField.Scope.GLOBAL
        and event.get(EventField.Event.KEY) == EventField.Event.NEW_ADDR
        and event.get(EventField.ADDRESS)
        and event.get(EventField.IFACE)
    )


def _get_event_family(event_family):
    if event_family == EventField.Family.IPV4:
        return 4
    if event_family == EventField.Family.IPV6:
        return 6
    return None
