#
# Copyright 2014-2016 Red Hat, Inc.
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

from __future__ import absolute_import
import operator
import signal
import subprocess

from testlib import VdsmTestCase
from testlib import expandPermutations, permutations

from vdsm import compat
from vdsm import procwatch


@expandPermutations
class ProcessWatcherTests(VdsmTestCase):

    @permutations([
        (['echo', '-n', '%s'], True, False),
        (['sh', '-c', 'echo -n "%s" >&2'], False, True),
    ])
    def test_receive(self, cmd, recv_out, recv_err):
        text = bytes('Hello World')
        received = bytearray()

        def recv_data(buffer):
            # cannot use received += buffer with a variable
            # defined in the parent function.
            operator.iadd(received, buffer)

        cmd[-1] = cmd[-1] % text

        process = self.start_process(cmd)
        watcher = procwatch.ProcessWatcher(
            process,
            recv_data if recv_out else self.unexpected_data,
            recv_data if recv_err else self.unexpected_data)

        while watcher.watching:
            watcher.receive()

        retcode = process.wait()

        self.assertEqual(retcode, 0)
        self.assertEqual(text, received)

    @permutations([
        (['cat'], True, False),
        (['sh', '-c', 'cat >&2'], False, True),
    ])
    def test_write(self, cmd, recv_out, recv_err):
        text = bytes('Hello World')
        received = bytearray()

        def recv_data(buffer):
            # cannot use received += buffer with a variable
            # defined in the parent function.
            operator.iadd(received, buffer)

        process = self.start_process(cmd)
        watcher = procwatch.ProcessWatcher(
            process,
            recv_data if recv_out else self.unexpected_data,
            recv_data if recv_err else self.unexpected_data)

        process.stdin.write(text)
        process.stdin.flush()
        process.stdin.close()

        while watcher.watching:
            watcher.receive()

        retcode = process.wait()

        self.assertEqual(retcode, 0)
        self.assertEqual(text, str(received))

    def test_timeout(self):
        process = self.start_process(["sleep", "5"])
        watcher = procwatch.ProcessWatcher(
            process, self.unexpected_data, self.unexpected_data)

        with self.assertElapsed(2):
            watcher.receive(2)

        self.assertEqual(watcher.watching, True)

        process.terminate()

        self.assertEqual(process.wait(), -signal.SIGTERM)

    @permutations((
        ('kill', -signal.SIGKILL),
        ('terminate', -signal.SIGTERM),
    ))
    def test_signals(self, method, expected_retcode):
        process = self.start_process(["sleep", "2"])
        watcher = procwatch.ProcessWatcher(
            process, self.unexpected_data, self.unexpected_data)

        getattr(process, method)()

        try:
            with self.assertElapsed(0):
                watcher.receive(2)
        finally:
            retcode = process.wait()

        self.assertEqual(retcode, expected_retcode)

    def unexpected_data(self, data):
        raise AssertionError("Unexpected data: %r" % data)

    def start_process(self, cmd):
        return compat.CPopen(cmd,
                             stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE)
