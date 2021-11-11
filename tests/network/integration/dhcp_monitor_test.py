# Copyright 2020-2021 Red Hat, Inc.
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
from unittest import mock
import threading
import time

import pytest

from vdsm.network import api as net_api
from vdsm.network import dhcp_monitor
from vdsm.network.initializer import init_unpriviliged_dhcp_monitor_ctx

from network.nettestlib import dummy_device
from network.nettestlib import Interface
from network.nettestlib import IpFamily
from network.nettestlib import parametrize_ip_families

IPv4_ADDRESS = '192.0.100.1'
IPv4_PREFIX_LEN = '24'
IPv6_ADDRESS = 'fdb3:84e5:4ff4:55e3::1010'
IPv6_PREFIX_LEN = '64'


@pytest.fixture
def nic0():
    with dummy_device() as nic:
        yield nic


@pytest.fixture
def dhcp_monitor_notifier():
    event_sink = FakeNotifier()
    with init_unpriviliged_dhcp_monitor_ctx(event_sink, net_api):
        yield event_sink


@pytest.fixture
def add_rule_mock():
    with mock.patch.object(
        net_api, 'add_dynamic_source_route_rules'
    ) as add_rule_mock:
        yield add_rule_mock


class FakeNotifier(object):
    def __init__(self):
        self.events = []

    def notify(self, event_id, params=None):
        self.events.append(event_id)


class AtomicAddressCounter(object):
    def __init__(self, families):
        self._addresses = self._init_addresses(families)
        self._lock = threading.Lock()

    def remove_addr(self, event):
        address = event.get(dhcp_monitor.EventField.ADDRESS)
        with self._lock:
            if address in self._addresses:
                self._addresses.remove(address)

    def is_empty(self):
        with self._lock:
            return not self._addresses

    @staticmethod
    def _init_addresses(families):
        addresses = set()
        if IpFamily.IPv4 in families:
            addresses.add(f'{IPv4_ADDRESS}/{IPv4_PREFIX_LEN}')
        if IpFamily.IPv6 in families:
            addresses.add(f'{IPv6_ADDRESS}/{IPv6_PREFIX_LEN}')
        return addresses


class TestMonitor(object):
    @parametrize_ip_families
    def test_add_ip_with_monitor(
        self, families, nic0, dhcp_monitor_notifier, add_rule_mock
    ):
        pool = dhcp_monitor.MonitoredItemPool.instance()
        pool.add((nic0, IpFamily.IPv4))
        pool.add((nic0, IpFamily.IPv6))

        with self._wait_for_events(families):
            self._configure_ip(nic0, families)

        assert len(dhcp_monitor_notifier.events) == len(families)
        events_set = set(dhcp_monitor_notifier.events)
        assert len(events_set) == 1
        assert '|net|host_conn|no_id' in events_set
        if IpFamily.IPv4 in families:
            add_rule_mock.assert_called_once_with(
                nic0, IPv4_ADDRESS, int(IPv4_PREFIX_LEN)
            )
        else:
            add_rule_mock.assert_not_called()

    @parametrize_ip_families
    def test_add_ip_without_monitor(
        self, families, nic0, dhcp_monitor_notifier, add_rule_mock
    ):
        with self._wait_for_events(families):
            self._configure_ip(nic0, families)

        assert not dhcp_monitor_notifier.events
        add_rule_mock.assert_not_called()

    @staticmethod
    def _configure_ip(device, families):
        interface = Interface.from_existing_dev_name(device)

        if IpFamily.IPv4 in families:
            interface.add_ip(IPv4_ADDRESS, IPv4_PREFIX_LEN, IpFamily.IPv4)
        if IpFamily.IPv6 in families:
            interface.add_ip(IPv6_ADDRESS, IPv6_PREFIX_LEN, IpFamily.IPv6)

    @contextmanager
    def _wait_for_events(self, families):
        counter = AtomicAddressCounter(families)
        monitor = dhcp_monitor.Monitor.instance()
        monitor.add_handler(lambda event: counter.remove_addr(event))

        yield

        # Timeout after 5 secs
        for _ in range(10):
            if counter.is_empty():
                return
            time.sleep(0.5)
