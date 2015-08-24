# Copyright 2013 Red Hat, Inc.
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
from testlib import VdsmTestCase as TestCaseBase
import monkeypatch
from vdsm import supervdsm
from vdsm import utils
from vdsm.constants import VDSM_USER
from pwd import getpwnam
import os

_test_list = []


@supervdsm.proxied_call
def foo(arg):
    _test_list.append(arg)


def dropPrivileges():
    vdsm_uid, vdsm_gid = getpwnam(VDSM_USER)[2:4:]
    os.setgroups([])
    os.setgid(vdsm_gid)
    os.setuid(vdsm_uid)
dropPrivileges()


class TestSuperVdsmRemotely(TestCaseBase):
    def testPingCall(self):
        proxy = supervdsm.getProxy()
        self.assertTrue(proxy.ping())

    @monkeypatch.MonkeyPatch(os, 'geteuid', lambda: 0)
    def test_proxied_call_run_as_root(self):
        o = object()
        foo(o)
        self.assertIn(o, _test_list)

    def test_proxied_call_fails_on_unknown_function(self):
        with self.assertRaises(ImportError):
            foo()

    def test_proxied_call_non_decorated_function(self):
        with self.assertRaises(RuntimeError):
            supervdsm.proxied_call(utils.tobool)(None)
