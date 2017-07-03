#
# Copyright 2016-2017 Red Hat, Inc.
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

from __future__ import absolute_import

import logging
import socket

from vdsm.config import config

import six


class Error(Exception):
    """Error fetching data from collectd."""


class NotConnected(Error):
    """Instance not connected to collectd."""


class Client(object):

    _log = logging.getLogger('vdsm.collectd.client')

    def __init__(self, path=None):
        self._path = (
            config.get('sampling', 'collectd_sock_path')
            if path is None else path
        )
        self._sock = None
        self._fobjr = None
        self._fobjw = None

    @property
    def path(self):
        return self._path

    def open(self):
        """
        Open the connection to collectd.
        You must call this before running any query to collectd.
        """
        self._log.debug('connecting to collectd through %r', self._path)
        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._sock.connect(self._path)
        if six.PY2:
            self._fobjr = self._sock.makefile('rb')
            self._fobjw = self._sock.makefile('wb')
        else:
            self._fobjr = self._sock.makefile('r')
            self._fobjw = self._sock.makefile('w')

    def close(self):
        """
        Close the connection to collectd.
        """
        if self._fobjr is not None:
            self._fobjr.close()
            self._fobjr = None
        if self._fobjw is not None:
            self._fobjw.close()
            self._fobjw = None
        if self._sock is not None:
            self._sock.close()
            self._sock = None
        self._log.debug('closed connection to collectd')

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def list(self):
        return self._cmd('LISTVAL')

    def get(self, key):
        return self._cmd('GETVAL "{key}"'.format(key=key))

    def _cmd(self, req):
        if self._fobjw is None or self._fobjr is None:
            raise NotConnected()
        count = self._send(req)
        return self._recv_lines(count)

    def _send(self, req):
        pkt = '{cmd}\n'.format(cmd=req)
        if six.PY2:
            pkt = pkt.encode('utf-8')
        self._log.debug('sending: %r', pkt)
        self._fobjw.write(pkt)
        self._fobjw.flush()
        ret, message = self._recv(sep=' ')
        status = int(ret)
        if status < 0:
            raise Error(message)
        return status

    def _recv(self, sep='='):
        res = self._fobjr.readline()  # already decoded str
        self._log.debug('received: %r', res)
        return res.strip().split(sep, 1) if res else []

    def _recv_lines(self, count):
        res = []
        for _ in range(count):
            key, val = self._recv()
            res.append(float(val))
        self._log.debug('returning data: %r', res)
        return res
