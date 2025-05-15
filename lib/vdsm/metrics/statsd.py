# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

import socket

_client = None


def start(address, port=8125):
    global _client
    if _client is None:
        _client = _StatsClient(address, port=port)


def stop():
    global _client
    if _client is not None:
        _client.close()


def send(report):
    for name, value in report.items():
        _client.gauge(name, value)


class _StatsClient(object):
    """
    Simple client that sends udp messages to stastd port in metrics format
    standard (based on http://metrics20.org/spec).

    Currently supports only gauge reports which is used in VDSM.
    """
    def __init__(self, host, port=8125, maxudpsize=512, ipv6=False):
        fam = socket.AF_INET6 if ipv6 else socket.AF_INET
        family, _, _, _, addr = socket.getaddrinfo(
            host, port, fam, socket.SOCK_DGRAM)[0]
        self._addr = addr
        self._sock = socket.socket(family, socket.SOCK_DGRAM)
        self._maxudpsize = maxudpsize

    def _send(self, data):
        try:
            self._sock.sendto(data, self._addr)
        except socket.error:
            # Keeping python-statsd behavior - we intintially avoid to log
            # any socket error to allow running this code without reachable
            # server.
            pass

    def close(self):
        self._sock.close()

    def gauge(self, stat, value):
        """
        Sending gauge report for specific metric in the format stat:value|g

        Args:
            stat (string): metric name decoded to utf-8
            value (int): numeric value for stat
        """
        data = '%s:%s|g' % (stat, value)
        self._send(data.encode('utf-8'))
