import os
from contextlib import contextmanager

from monkeypatch import MonkeyPatch
from testlib import VdsmTestCase as TestCaseBase
from testlib import make_config
from testlib import expandPermutations, permutations
from vdsm import commands
from vdsm import utils
from vdsm.password import ProtectedPassword

from storage import iscsi
from storage import iscsiadm


def fake_rescan(timeout):
    def func():
        proc = commands.execCmd(["sleep", str(timeout)], sync=False)
        return utils.AsyncProcessOperation(proc)
    return func


class RescanTimeoutTests(TestCaseBase):

    @contextmanager
    def assertMaxDuration(self, maxtime):
        start = utils.monotonic_time()
        try:
            yield
        finally:
            elapsed = utils.monotonic_time() - start
            if maxtime < elapsed:
                self.fail("Operation was too slow %.2fs > %.2fs" %
                          (elapsed, maxtime))

    @MonkeyPatch(iscsiadm, 'session_rescan_async', fake_rescan(0.1))
    def testWait(self):
        with self.assertMaxDuration(0.3):
            iscsi.rescan()

    @MonkeyPatch(iscsiadm, 'session_rescan_async', fake_rescan(2))
    @MonkeyPatch(iscsi, 'config',
                 make_config([("irs", "scsi_rescan_maximal_timeout", "1")]))
    def testTimeout(self):
        with self.assertMaxDuration(1.2):
            iscsi.rescan()


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


@expandPermutations
class TestChapCredentialsEquality(TestCaseBase):

    @permutations([
        (None, None),
        (None, "password"),
        ("username", None),
        ("usernae", "password"),
    ])
    def test_eq_equal(self, username, password):
        c1 = iscsi.ChapCredentials(username, protected(password))
        c2 = iscsi.ChapCredentials(username, protected(password))
        self.assertTrue(c1 == c2, "%s should equal %s" % (c1, c2))

    def test_eq_subclass(self):
        class Subclass(iscsi.ChapCredentials):
            pass
        c1 = iscsi.ChapCredentials("username", protected("password"))
        c2 = Subclass("username", protected("password"))
        self.assertFalse(c1 == c2, "%s should not equal %s" % (c1, c2))

    @permutations([
        ("a", "a", "a", "b"),
        ("a", "b", "a", "a"),
    ])
    def test_eq_different(self, user1, user2, pass1, pass2):
        c1 = iscsi.ChapCredentials(user1, protected(pass1))
        c2 = iscsi.ChapCredentials(user2, protected(pass2))
        self.assertFalse(c1 == c2, "%s should not equal %s" % (c1, c2))

    @permutations([
        (None, None),
        (None, "password"),
        ("username", None),
        ("usernae", "password"),
    ])
    def test_ne_equal(self, username, password):
        c1 = iscsi.ChapCredentials(username, protected(password))
        c2 = iscsi.ChapCredentials(username, protected(password))
        self.assertFalse(c1 != c2, "%s should equal %s" % (c1, c2))


@expandPermutations
class TestChapCredentialsHash(TestCaseBase):

    @permutations([
        (None, None),
        (None, "password"),
        ("username", None),
        ("usernae", "password"),
    ])
    def test_equal_same_hash(self, username, password):
        c1 = iscsi.ChapCredentials(username, protected(password))
        c2 = iscsi.ChapCredentials(username, protected(password))
        self.assertEqual(hash(c1), hash(c2))

    def test_subclass_different_hash(self):
        class Subclass(iscsi.ChapCredentials):
            pass
        c1 = iscsi.ChapCredentials("username", protected("password"))
        c2 = Subclass("username", protected("password"))
        self.assertNotEqual(hash(c1), hash(c2))

    @permutations([
        ("a", "a", "a", "b"),
        ("a", "b", "a", "a"),
    ])
    def test_not_equal_different_hash(self, user1, user2, pass1, pass2):
        c1 = iscsi.ChapCredentials(user1, protected(pass1))
        c2 = iscsi.ChapCredentials(user2, protected(pass2))
        self.assertNotEqual(hash(c1), hash(c2))


def protected(password):
    if password is None:
        return None
    return ProtectedPassword(password)
