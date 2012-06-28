#
# Copyright 2012 Red Hat, Inc.
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

import time

from testrunner import VdsmTestCase as TestCaseBase
from nose.plugins.skip import SkipTest

from vdsm.config import config
from vdsm import vdscli

if not config.getboolean('vars', 'xmlrpc_enable'):
    raise SkipTest("XML-RPC Bindings are disabled")


class XMLRPCTest(TestCaseBase):
    UPSTATES = frozenset(('Up', 'Powering up', 'Running'))

    def setUp(self):
        self.s = vdscli.connect()

    def testGetCaps(self):
        r = self.s.getVdsCapabilities()
        self.assertEquals(r['status']['code'], 0)

    def assertVmUp(self, vmid):
        r = self.s.getVmStats(vmid)
        self.assertEquals(r['status']['code'], 0)
        self.myAssertIn(r['statsList'][0]['status'], self.UPSTATES)

    def myAssertIn(self, member, container, msg=None):
        "Poor man's reimplementation of Python2.7's unittest.assertIn"

        if hasattr(self, 'assertIn'):
            return self.assertIn(member, container, msg)

        if msg is None:
            msg = '%r not found in %r' % (member, container)

        self.assertTrue(member in container, msg)

    def retryAssertVmUp(self, vmid, retries, delay=1):
        for t in xrange(retries):
            try:
                self.assertVmUp(vmid)
                break
            except AssertionError:
                pass
            time.sleep(delay)
        else:
            self.assertVmUp(vmid)

    def testStartEmptyVM(self):
        VMID = '66666666-ffff-4444-bbbb-333333333333'

        r = self.s.create({'memSize': '100', 'display': 'vnc', 'vmId': VMID,
                           'vmName': 'foo'})
        self.assertEquals(r['status']['code'], 0)
        try:
            self.retryAssertVmUp(VMID, 20)
        finally:
            # FIXME: if the server dies now, we end up with a leaked VM.
            r = self.s.destroy(VMID)
            self.assertEquals(r['status']['code'], 0)
