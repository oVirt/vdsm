# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

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
