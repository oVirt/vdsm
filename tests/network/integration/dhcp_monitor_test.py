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
import json
import os
import socket
import time

import pytest

from vdsm.network import dhcp_monitor

SOCKET = 'monitor.sock'

EVENT1 = {'event': 'event'}


@pytest.fixture(scope='function')
def monitor(tmp_path):
    socket_path = str(tmp_path / SOCKET)
    with monitor_ctx(socket_path) as monitor:
        yield monitor


@contextmanager
def monitor_ctx(socket_path):
    monitor = dhcp_monitor.Monitor.instance(socket_path=socket_path)
    monitor.start()
    try:
        yield monitor
    finally:
        dhcp_monitor.clear_monitor()


@pytest.fixture(scope='function')
def client(tmp_path):
    socket_path = str(tmp_path / SOCKET)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.connect(socket_path)
        yield client


class TestMonitor(object):
    def test_socket_creation_and_removal(self, tmp_path):
        socket_path = str(tmp_path / SOCKET)
        with monitor_ctx(socket_path):
            assert os.path.exists(socket_path)
        assert not os.path.exists(socket_path)

    def test_action_handler(self, monitor, client):
        events = []
        monitor.add_handler(lambda event: events.append(event))
        self._send_data(client, EVENT1)
        time.sleep(0.1)

        assert len(events) == 1
        assert events[0] == EVENT1

    @staticmethod
    def _send_data(client, content):
        client.sendall(bytes(json.dumps(content), 'utf-8'))
