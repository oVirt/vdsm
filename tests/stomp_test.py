#
# Copyright 2015-2017 Red Hat, Inc.
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
import threading
from uuid import uuid4

from testlib import VdsmTestCase as TestCaseBase, \
    expandPermutations, \
    permutations, \
    dummyTextGenerator

import yajsonrpc
from integration.jsonRpcHelper import constructAcceptor
from yajsonrpc.stompreactor import StandAloneRpcClient
from vdsm import utils

from testValidation import brokentest

from integration.sslhelper import DEAFAULT_SSL_CONTEXT


CALL_TIMEOUT = 15
_USE_SSL = [[True], [False]]


class Schema(object):

    def verify_event_params(self, sub_id, args):
        pass


class _SampleBridge(object):
    cif = None
    event_schema = Schema()

    def echo(self, text):
        return text

    def event(self):
        self.cif.notify('vdsm.event', {'content': True})

    def register_server_address(self, server_address):
        self.server_address = server_address

    def unregister_server_address(self):
        self.server_address = None

    def dispatch(self, method):
        try:
            return getattr(self, method)
        except AttributeError:
            raise yajsonrpc.JsonRpcMethodNotFoundError(method=method)


@expandPermutations
class StompTests(TestCaseBase):

    @brokentest('This test randomly fails on CI with JsonRpcNoResponseError')
    @permutations([
        # size, use_ssl
        (1024, True),
        (1024, False),
        (4096, True),
        (4096, False),
        (16384, True),
        (16384, False),
    ])
    def test_echo(self, size, use_ssl):
        data = dummyTextGenerator(size)

        with constructAcceptor(self.log, use_ssl, _SampleBridge()) as acceptor:
            sslctx = DEAFAULT_SSL_CONTEXT if use_ssl else None

            with utils.running(StandAloneRpcClient(acceptor._host,
                                                   acceptor._port,
                                                   'jms.topic.vdsm_requests',
                                                   str(uuid4()),
                                                   sslctx)) as client:
                self.assertEqual(client.callMethod('echo', (data,),
                                                   str(uuid4())),
                                 data)

    @brokentest('This test randomly fails on CI with JsonRpcNoResponseError')
    @permutations(_USE_SSL)
    def test_event(self, use_ssl):
        done = threading.Event()

        with constructAcceptor(self.log, use_ssl, _SampleBridge(),
                               'jms.queue.events') as acceptor:
            sslctx = DEAFAULT_SSL_CONTEXT if use_ssl else None
            client = StandAloneRpcClient(acceptor._host, acceptor._port,
                                         'jms.topic.vdsm_requests',
                                         'jms.queue.events', sslctx, False)

            def callback(client, event, params):
                self.assertEqual(event, 'vdsm.event')
                self.assertEqual(params['content'], True)
                done.set()

            client.registerEventCallback(callback)
            client.callMethod("event", [], str(uuid4()))
            done.wait(timeout=CALL_TIMEOUT)
            self.assertTrue(done.is_set())
