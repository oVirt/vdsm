# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

from unittest import mock

from vdsm.metrics import statsd
from testlib import VdsmTestCase as TestCaseBase


class StatsdModuleTest(TestCaseBase):
    @classmethod
    def setup_class(cls):
        cls.old_socket = statsd.socket.socket
        cls.mock_socket = statsd.socket.socket = mock.Mock()
        try:
            statsd.start('localhost')
            cls._address = ('127.0.0.1', 8125)
        except:
            statsd.socket.socket = cls.old_socket

    @classmethod
    def teardown_class(cls):
        statsd.socket.socket = cls.old_socket

    def setUp(self):
        self.mock_socket.reset_mock()

    def test_send_single(self):
        data = {'hello': 3}
        statsd.send(data)
        self.mock_socket.return_value.sendto.assert_called_once_with(
            b'hello:3|g', self._address)

    def test_send_unicode(self):
        data = {'\xd7\xa9\xd7\x9c\xd7\x95\xd7\x9d': 3}
        statsd.send(data)
        self.mock_socket.return_value.sendto.assert_called_once_with(
            b'\xd7\xa9\xd7\x9c\xd7\x95\xd7\x9d:3|g', self._address)

    def test_send_mixed_chars(self):
        data = {'hello.\xd7\xa9\xd7\x9c\xd7\x95\xd7\x9d.ma': 3}
        statsd.send(data)
        self.mock_socket.return_value.sendto.assert_called_once_with(
            b'hello.\xd7\xa9\xd7\x9c\xd7\x95\xd7\x9d.ma:3|g', self._address)

    def test_send_long_metric_name(self):
        long_metric_name = ".".join(["1234567890"] * 12)
        data = {long_metric_name: 3}
        statsd.send(data)
        self.mock_socket.return_value.sendto.assert_called_once_with(
            b'%s:3|g' % long_metric_name, self._address)

    def test_send_multiple(self):
        data = {'hello': 7, 'goodbye': 11}
        statsd.send(data)
        calls = [mock.call(b'hello:7|g', self._address),
                 mock.call(b'goodbye:11|g', self._address)]
        self.mock_socket.return_value.sendto.assert_has_calls(calls)
