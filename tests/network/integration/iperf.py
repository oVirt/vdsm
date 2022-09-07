# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

import functools
import json
import os
import time

import pytest

from vdsm.network import cmd
from vdsm.network.ipwrapper import netns_exec

_IPERF3_BINARY = 'iperf3'


class IperfServer(object):
    """Starts iperf as an async process"""

    def __init__(self, host, network_ns):
        """host: the IP address for the server to listen on.
        network_ns: an optional network namespace for the server to run in.
        """
        self._bind_to = host
        self._net_ns = network_ns
        self._popen = None

    def start(self):
        cmd = [_IPERF3_BINARY, '--server', '--bind', self._bind_to]
        self._popen = netns_exec(self._net_ns, cmd)

    def stop(self):
        self._popen.terminate()
        self._popen.wait()


class IperfClient(object):
    def __init__(self, server_ip, bind_to, test_time, threads=1):
        """The client generates a machine readable json output that is set in
        _raw_output upon completion, and can be read using the 'out' property.
        server_ip: the ip of the corresponding iperf server
        bind_to: IP address of the client
        test_time: in seconds
        """
        self._server_ip = server_ip
        self._bind_to = bind_to
        self._test_time = test_time
        self._threads = threads
        self._raw_output = None

    def start(self):
        cmds = [
            _IPERF3_BINARY,
            '--client',
            self._server_ip,
            '--version4',  # only IPv4
            '--time',
            str(self._test_time),
            '--parallel',
            str(self._threads),
            '--bind',
            self._bind_to,
            '--zerocopy',  # use less cpu
            '--json',
        ]
        rc, self._raw_output, err = cmd.exec_sync(cmds)
        if rc == 1 and 'No route to host' in self.out['error']:
            # it seems that it takes some time for the routes to get updated
            # on the os so that we don't get this error, hence the horrific
            # sleep here.
            # TODO: Investigate, understand, and remove this sleep.
            time.sleep(3)
            rc, self._raw_output, err = cmd.exec_sync(cmds)
        if rc:
            raise Exception(
                'iperf3 client failed: cmd=%s, rc=%s, out=%s, '
                'err=%s' % (' '.join(cmds), rc, self._raw_output, err)
            )

    @property
    def out(self):
        return json.loads(self._raw_output)


def _check_iperf():
    if not os.access(_IPERF3_BINARY, os.X_OK):
        pytest.skip(
            "Cannot run %r: %s\nDo you have iperf3 installed?" % _IPERF3_BINARY
        )


def requires_iperf3(f):
    @functools.wraps(f)
    def wrapper(*a, **kw):
        _check_iperf()
        return f(*a, **kw)

    return wrapper
