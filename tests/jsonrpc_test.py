#
# Copyright 2017 Red Hat, Inc.
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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#

from __future__ import absolute_import
from __future__ import division
import json
from yajsonrpc import JsonRpcRequest, JsonRpcServer

from vdsm.common import exception

from testlib import VdsmTestCase


class FakeContext(object):

    def requestDone(self, res):
        self._res = res

    @property
    def response(self):
        return self._res


class ServerTests(VdsmTestCase):

    def test_full_pool(self):
        def thread_factory(callable):
            raise exception.ResourceExhausted("Too many tasks",
                                              resource="test", current_tasks=0)

        ctx = FakeContext()
        request = JsonRpcRequest.decode(
            '{"jsonrpc":"2.0","method":"Host.stats","params":{},"id":"943"}')

        server = JsonRpcServer(None, 0, None, threadFactory=thread_factory)
        server._runRequest(ctx, request)

        error = ctx.response.toDict().get('error')
        self.assertEqual(1100, error.get('code'))

        msg = error.get('message')
        self.assertTrue(msg.startswith("Not enough resources:"))

        # not deterministic order in a dict so we need to parse
        reason = json.loads(msg[22:].replace("'", '"'))
        self.assertEqual({"reason": "Too many tasks",
                          "resource": "test",
                          "current_tasks": 0}, reason)
