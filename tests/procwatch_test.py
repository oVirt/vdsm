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

from testlib import VdsmTestCase
from testlib import expandPermutations, permutations

from vdsm import compat
from vdsm import procwatch


@expandPermutations
class CommandStreamTests(VdsmTestCase):

    def assertUnexpectedCall(self, data):
        raise AssertionError("Unexpected data: %r" % data)

    def _startCommand(self, command):
        return compat.CPopen(command)

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

        c = self._startCommand(cmd)
        p = procwatch.CommandStream(
            c,
            recv_data if recv_out else self.assertUnexpectedCall,
            recv_data if recv_err else self.assertUnexpectedCall)

        while not p.closed:
            p.receive()

        retcode = c.wait()

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

        c = self._startCommand(cmd)
        p = procwatch.CommandStream(
            c,
            recv_data if recv_out else self.assertUnexpectedCall,
            recv_data if recv_err else self.assertUnexpectedCall)

        c.stdin.write(text)
        c.stdin.flush()
        c.stdin.close()

        while not p.closed:
            p.receive()

        retcode = c.wait()

        self.assertEqual(retcode, 0)
        self.assertEqual(text, str(received))

    def test_timeout(self):
        c = self._startCommand(["sleep", "5"])
        p = procwatch.CommandStream(c, self.assertUnexpectedCall,
                                    self.assertUnexpectedCall)

        with self.assertElapsed(2):
            p.receive(2)

        self.assertEqual(p.closed, False)

        c.terminate()

        self.assertEqual(c.wait(), -signal.SIGTERM)

    @permutations((
        ('kill', -signal.SIGKILL),
        ('terminate', -signal.SIGTERM),
    ))
    def test_signals(self, method, expected_retcode):
        c = self._startCommand(["sleep", "2"])
        p = procwatch.CommandStream(c, self.assertUnexpectedCall,
                                    self.assertUnexpectedCall)

        getattr(c, method)()

        try:
            with self.assertElapsed(0):
                p.receive(2)
        finally:
            retcode = c.wait()

        self.assertEqual(retcode, expected_retcode)
