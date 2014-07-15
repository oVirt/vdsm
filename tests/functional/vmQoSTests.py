#
# Copyright 2014 Red Hat, Inc.
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

import contextlib
import time

import virtTests
from momTests import skipNoMOM
from virtTests import VirtTestBase, RunningVm, requireKVM, VM_MINIMAL_UPTIME
from utils import VdsProxy, SUCCESS


def setUpModule():
    # to make pyflakes happy
    virtTests.setUpModule()


def tearDownModule():
    # to make pyflakes happy
    virtTests.tearDownModule()


class VMQosTests(VirtTestBase):

    def setUp(self):
        self.s = self.vdsm = VdsProxy()

    def assertCallSucceeded(self, result):
        code, message = result
        self.assertEquals(code, SUCCESS,
                          'error code: %s, message: %s' % (code, message))

    @requireKVM
    @skipNoMOM
    def testSmallVMBallooning(self):
        devices = [{'device': 'memballoon',
                    'type': 'balloon',
                    'specParams': {'model': 'virtio'}}]
        customization = {'vmId': '77777777-ffff-3333-bbbb-555555555555',
                         'vmName': 'vdsm_testBalloonVM', 'display': 'qxl',
                         'devices': devices}
        policy = {'balloon': """
            (def set_guest (guest)
            {
            (guest.Control "balloon_target" 0)
            })
            (with Guests guest (set_guest guest))"""}

        with RunningVm(self.vdsm, customization) as vm:
            self._waitForStartup(vm, VM_MINIMAL_UPTIME)
            with self._balloonPolicy(policy):
                time.sleep(12)  # MOM policy engine wake up every 10s
                status, msg, stats = self.vdsm.getVmStats(vm)
                self.assertEqual(status, SUCCESS, msg)
                self.assertEqual(stats['balloonInfo']['balloon_cur'], 0)

    @contextlib.contextmanager
    def _balloonPolicy(self, policy):
        self.assertCallSucceeded(self.vdsm.setMOMPolicy(policy))
        try:
            yield
        finally:
            self.assertCallSucceeded(self.vdsm.resetMOMPolicy())
