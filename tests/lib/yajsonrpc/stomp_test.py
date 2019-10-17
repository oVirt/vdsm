#
# Copyright 2015-2019 Red Hat, Inc.
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
from six.moves import queue
from uuid import uuid4

from testlib import VdsmTestCase as TestCaseBase, \
    expandPermutations, \
    permutations, \
    dummyTextGenerator

from testValidation import broken_on_ci

import yajsonrpc
from integration.jsonRpcHelper import constructAcceptor
from yajsonrpc.stompclient import StandAloneRpcClient
from vdsm import utils

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

    @broken_on_ci(
        "Fails randomly in oVirt CI, see https://gerrit.ovirt.org/c/95899/")
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

            with utils.closing(StandAloneRpcClient(acceptor._host,
                                                   acceptor._port,
                                                   'jms.topic.vdsm_requests',
                                                   str(uuid4()),
                                                   sslctx, False)) as client:
                self.assertEqual(client.callMethod('echo', (data,),
                                                   str(uuid4())),
                                 data)

    @permutations(_USE_SSL)
    def test_event(self, use_ssl):
        with constructAcceptor(self.log, use_ssl, _SampleBridge(),
                               'jms.queue.events') as acceptor:
            sslctx = DEAFAULT_SSL_CONTEXT if use_ssl else None
            with utils.closing(StandAloneRpcClient(acceptor._host,
                                                   acceptor._port,
                                                   'jms.topic.vdsm_requests',
                                                   'jms.queue.events',
                                                   sslctx, False)) as client:

                event_queue = queue.Queue()
                custom_topic = 'jms.queue.events'
                client.subscribe(custom_topic, event_queue)

                client.callMethod("event", [], str(uuid4()))

                try:
                    event, event_params = event_queue.get(timeout=CALL_TIMEOUT)
                except queue.Empty:
                    self.fail("Event queue timed out.")
                self.assertEqual(event, 'vdsm.event')
                self.assertEqual(event_params['content'], True)
