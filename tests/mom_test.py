# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

from unittest import TestCase
import logging
import shutil
import tempfile
import threading
from vdsm.common.units import MiB
from vdsm.momIF import MomClient
import os.path
import monkeypatch

import unixrpc_testlib

from vdsm.common import cpuarch

MOM_CONF = "/dev/null"
MOM_SOCK = "test_mom_vdsm.sock"


class DummyMomApi(object):
    def __init__(self):
        self.last_policy_name = None
        self.last_policy_content = None

    def ping(self):
        return True

    def setNamedPolicy(self, policy_name, content):
        self.last_policy_name = policy_name
        self.last_policy_content = content

    def setPolicy(self, content):
        self.last_policy_name = None
        self.last_policy_content = content

    def getStatistics(self):
        return {
            "host": {
                "ksm_run": 0,
                "ksm_merge_across_nodes": 1,
                "ksm_pages_to_scan": 5,
                "ksm_pages_sharing": 100,
                "ksmd_cpu_usage": 15
            }
        }


class BrokenMomApi(object):
    def ping(self):
        return False


# Each time mom server or client is created, a new logging.StreamHanlder is
# added to the "mom" logger. This monkey-patching remove loggers and handlers
# added during the tests.
@monkeypatch.MonkeyClass(logging.getLogger().manager, "loggerDict", {})
class MomPolicyTests(TestCase):

    _TMP_DIR = tempfile.gettempdir()

    def setUp(self):
        self._tmp_dir = tempfile.mkdtemp(dir=self._TMP_DIR)
        self._sock_path = os.path.join(self._tmp_dir, MOM_SOCK)

    def tearDown(self):
        shutil.rmtree(self._tmp_dir)

    def _getMomClient(self):
        cli = MomClient(self._sock_path)
        cli.connect()
        return cli

    def _getMomServer(self, api_class=DummyMomApi):
        server = unixrpc_testlib.UnixXmlRpcServer(self._sock_path)
        api = api_class()
        server.register_instance(api)
        t = threading.Thread(target=server.serve_forever)
        return server, t, api

    def _stopMomServer(self, server, t):
        if t.is_alive():
            server.shutdown()
            t.join()

    def testSetPolicyParameters(self):
        server, thread, api = self._getMomServer()

        try:
            client = self._getMomClient()
            thread.start()
            client.setPolicyParameters({"a": 5, "b": True, "c": "test"})
        finally:
            self._stopMomServer(server, thread)

        expected = "(set a 5)\n(set c 'test')\n(set b True)"

        self.assertEqual(api.last_policy_name, "01-parameters")
        self._check_policy_equal(api.last_policy_content, expected)

    def testSetPolicy(self):
        server, thread, api = self._getMomServer()

        try:
            client = self._getMomClient()
            thread.start()
            expected = "(set a 5)\n(set c 'test')\n(set b True)"
            client.setPolicy(expected)
        finally:
            self._stopMomServer(server, thread)

        self.assertEqual(api.last_policy_name, None)
        self.assertEqual(api.last_policy_content, expected)

    def testGetStatus(self):
        server, thread, api = self._getMomServer()

        try:
            client = self._getMomClient()
            thread.start()
            self.assertEqual("active", client.getStatus())
        finally:
            self._stopMomServer(server, thread)

    def testGetStatusFailing(self):
        server, thread, api = self._getMomServer(BrokenMomApi)

        try:
            client = self._getMomClient()
            thread.start()
            self.assertEqual("inactive", client.getStatus())
        finally:
            self._stopMomServer(server, thread)

    def testNoServerRunning(self):
        client = self._getMomClient()
        self.assertEqual(client.getStatus(), 'inactive')

    def testGetKsmStats(self):
        server, thread, api = self._getMomServer()

        try:
            client = self._getMomClient()
            thread.start()
            stats = client.getKsmStats()
        finally:
            self._stopMomServer(server, thread)

        expected = {
            "ksmCpu": 15,
            "ksmMergeAcrossNodes": True,
            "ksmState": False,
            "ksmPages": 5,
            "memShared": 100 * cpuarch.PAGE_SIZE_BYTES // MiB
        }

        self.assertEqual(stats, expected)

    def _check_policy_equal(self, policy_a, policy_b):
        self.assertEqual(
            sorted(set(policy_a.split('\n'))),
            sorted(set(policy_b.split('\n'))))
