# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

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

from integration.sslhelper import generate_key_cert_pair, create_ssl_context


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

    def run(self, result=None):
        with generate_key_cert_pair() as key_cert_pair:
            key_file, cert_file = key_cert_pair
            self.ssl_ctx = create_ssl_context(key_file, cert_file)
            super(TestCaseBase, self).run(result)

    @broken_on_ci(
        "Fails randomly in oVirt CI, see https://gerrit.ovirt.org/c/95899/",
        name="TRAVIS_CI")
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
        ssl_ctx = self.ssl_ctx if use_ssl else None

        with constructAcceptor(self.log, ssl_ctx, _SampleBridge()) as acceptor:
            with utils.closing(StandAloneRpcClient(acceptor._host,
                                                   acceptor._port,
                                                   'jms.topic.vdsm_requests',
                                                   str(uuid4()),
                                                   ssl_ctx, False)) as client:
                self.assertEqual(client.callMethod('echo', (data,),
                                                   str(uuid4())),
                                 data)

    @permutations(_USE_SSL)
    def test_event(self, use_ssl):
        ssl_ctx = self.ssl_ctx if use_ssl else None

        with constructAcceptor(self.log, ssl_ctx, _SampleBridge(),
                               'jms.queue.events') as acceptor:
            with utils.closing(StandAloneRpcClient(acceptor._host,
                                                   acceptor._port,
                                                   'jms.topic.vdsm_requests',
                                                   'jms.queue.events',
                                                   ssl_ctx, False)) as client:

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
