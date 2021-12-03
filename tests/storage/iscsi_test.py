#
# Copyright 2017-2021 Red Hat, Inc.
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
from __future__ import division

import os

from contextlib import contextmanager
from time import sleep

from monkeypatch import MonkeyPatch
from testlib import VdsmTestCase
from testlib import make_config
from testlib import expandPermutations, permutations
from vdsm.common import time
from vdsm.common.password import HiddenValue
from vdsm.storage import iscsi
from vdsm.storage import iscsiadm


def fake_rescan(actual):
    def func(timeout):
        if actual <= timeout:
            sleep(actual)
        else:
            sleep(timeout)
            raise iscsiadm.IscsiSessionRescanTimeout(-1, timeout)
    return func


class TestRescanTimeout(VdsmTestCase):

    @contextmanager
    def assertMaxDuration(self, maxtime):
        start = time.monotonic_time()
        try:
            yield
        finally:
            elapsed = time.monotonic_time() - start
            if maxtime < elapsed:
                self.fail("Operation was too slow %.2fs > %.2fs" %
                          (elapsed, maxtime))

    @MonkeyPatch(iscsiadm, 'session_rescan', fake_rescan(0.1))
    @MonkeyPatch(iscsi, 'config',
                 make_config([("irs", "scsi_rescan_maximal_timeout", "1")]))
    def testWait(self):
        with self.assertMaxDuration(0.3):
            iscsi.rescan()

    @MonkeyPatch(iscsiadm, 'session_rescan', fake_rescan(2))
    @MonkeyPatch(iscsi, 'config',
                 make_config([("irs", "scsi_rescan_maximal_timeout", "1")]))
    def testTimeout(self):
        with self.assertMaxDuration(1.2):
            iscsi.rescan()


class TestIscsiAdm(VdsmTestCase):
    def testIfaceList(self):
        dirName = os.path.dirname(os.path.realpath(__file__))
        path = os.path.join(dirName, "iscsiadm_-m_iface.out")
        with open(path) as f:
            out = f.read()

        Iface = iscsi.iscsiadm.Iface

        res = (Iface('default', 'tcp', None, None, None, None),
               Iface('iser', 'iser', None, None, None, None),
               Iface('eth1', 'tcp', None, None, 'SAN1', None),
               Iface('eth2', 'tcp', None, None, 'eth2', None))

        self.assertEqual(tuple(iscsi.iscsiadm.iface_list(out=out)), res)


@expandPermutations
class TestChapCredentialsEquality(VdsmTestCase):

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
class TestChapCredentialsHash(VdsmTestCase):

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
    return HiddenValue(password)


@expandPermutations
class TestIscsiPortal(VdsmTestCase):

    @permutations([
        ("192.0.2.23", 5003, "192.0.2.23:5003"),
        ("3ffe:2a00:100:7031::1", 3260, "[3ffe:2a00:100:7031::1]:3260"),
        ("::192.0.2.5", 3260, "[::192.0.2.5]:3260"),
        ("moredisks.example.com", 5003, "moredisks.example.com:5003"),
        ("fe80::5054:ff:fe69:d588%ens3", 3260,
         "[fe80::5054:ff:fe69:d588%ens3]:3260")
    ])
    def test_str(self, hostname, port, expected):
        self.assertEqual(str(iscsi.IscsiPortal(hostname, port)), expected)

    @permutations([
        ("192.0.2.23", 5003, False),
        ("3ffe:2a00:100:7031::1", 3260, True),
        ("::192.0.2.5", 3260, True),
        ("moredisks.example.com", 5003, False),
        ("fe80::5054:ff:fe69:d588%ens3", 3260, True)
    ])
    def test_is_ipv6(self, hostname, port, expected):
        target = iscsi.IscsiPortal(hostname, port)
        self.assertEqual(target.is_ipv6(), expected)


class TestIscsiTarget(VdsmTestCase):

    def test_str(self):
        target = iscsi.IscsiTarget(
            iscsi.IscsiPortal(
                "3ffe:2a00:100:7031::1",
                3260),
            1, "iqn.2014-06.com.example:t1")
        self.assertEqual(
            str(target),
            "[3ffe:2a00:100:7031::1]:3260,1 iqn.2014-06.com.example:t1")

    def test_address(self):
        target = iscsi.IscsiTarget(
            iscsi.IscsiPortal(
                "3ffe:2a00:100:7031::1",
                3260),
            2, "iqn.2014-06.com.example:t1")
        self.assertEqual(target.address, "[3ffe:2a00:100:7031::1]:3260,2")
