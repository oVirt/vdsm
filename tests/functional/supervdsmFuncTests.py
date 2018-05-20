# Copyright 2013-2017 Red Hat, Inc.
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
from testlib import forked
from testlib import VdsmTestCase as TestCaseBase
import testValidation
from vdsm.common import supervdsm
from vdsm.constants import VDSM_USER
from pwd import getpwnam
import os

import six


def dropPrivileges():
        vdsm_uid, vdsm_gid = getpwnam(VDSM_USER)[2:4:]
        os.setgroups([])
        os.setgid(vdsm_gid)
        os.setuid(vdsm_uid)


class TestSuperVdsmRemotly(TestCaseBase):

    @forked
    @testValidation.ValidateRunningAsRoot
    def testPingCall(self):
        dropPrivileges()
        proxy = supervdsm.getProxy()
        self.assertTrue(proxy.ping())

    # This requires environment with tmpfs mounted to /sys/kernel/mm/ksm
    @forked
    @testValidation.ValidateRunningAsRoot
    def testKsmAction(self):
        dropPrivileges()
        proxy = supervdsm.getProxy()
        ksmParams = {"run": 0,
                     "merge_across_nodes": 1,
                     "sleep_millisecs": 0xffff,
                     "pages_to_scan": 0xffff}
        proxy.ksmTune(ksmParams)

        for k, v in six.iteritems(ksmParams):
            self.assertEqual(str(v),
                             open("/sys/kernel/mm/ksm/%s" % k, "r").read())
