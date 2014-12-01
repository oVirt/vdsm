import os
import threading
import time
from contextlib import contextmanager

from testlib import VdsmTestCase as TestCaseBase

from storage import iscsi


class AsyncStubOperation(object):
    def __init__(self, timeout):
        self._evt = threading.Event()
        if timeout == 0:
            self._evt.set()
        else:
            threading.Timer(timeout, self._evt.set)

    def wait(self, timeout=None, cond=None):
        if cond is not None:
            raise Exception("TODO!!!")

        self._evt.wait(timeout)

    def stop(self):
        self._evt.set()

    def result(self):
        if self._evt.is_set():
            return (None, None)
        else:
            return None


class RescanTimeoutTests(TestCaseBase):
    def setUp(self):
        self._iscsiadm_rescan_async = \
            iscsi.iscsiadm.session_rescan_async
        iscsi.iscsiadm.session_rescan_async = self._iscsi_stub
        self._timeout = 0

    def tearDown(self):
        iscsi.iscsiadm.session_rescan_async = \
            self._iscsiadm_rescan_async

    def _iscsi_stub(self):
        return AsyncStubOperation(self._timeout)

    @contextmanager
    def assertMaxDuration(self, maxtime):
        start = time.time()
        try:
            yield
        finally:
            end = time.time()
            elapsed = end - start
            if maxtime < elapsed:
                self.fail("Operation was too slow %fs > %fs" %
                          (elapsed, maxtime))

    @contextmanager
    def assertMinDuration(self, mintime):
        start = time.time()
        try:
            yield
        finally:
            end = time.time()
            elapsed = end - start
            if mintime > elapsed:
                self.fail("Operation was too fast %fs > %fs" %
                          (elapsed, mintime))

    def testFast(self):
        self._timeout = 0
        with self.assertMinDuration(2):
            iscsi.rescan(2, 4)

    def testSlow(self):
        self._timeout = 60
        with self.assertMaxDuration(3):
            iscsi.rescan(1, 2)


class IscsiAdmTests(TestCaseBase):
    def testIfaceList(self):
        dirName = os.path.dirname(os.path.realpath(__file__))
        path = os.path.join(dirName, "iscsiadm_-m_iface.out")
        with open(path) as f:
            out = f.read().splitlines()

        Iface = iscsi.iscsiadm.Iface

        res = (Iface('default', 'tcp', None, None, None, None),
               Iface('iser', 'iser', None, None, None, None),
               Iface('eth1', 'tcp', None, None, 'SAN1', None),
               Iface('eth2', 'tcp', None, None, 'eth2', None))

        self.assertEqual(tuple(iscsi.iscsiadm.iface_list(out=out)), res)
