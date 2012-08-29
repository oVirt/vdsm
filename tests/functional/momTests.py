#
# Copyright IBM Corp. 2012
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
import imp
import random
import time

import testValidation
from testrunner import VdsmTestCase as TestCaseBase
from nose.plugins.skip import SkipTest
from vdsm import vdscli

try:
    imp.find_module('mom')
except ImportError:
    raise SkipTest('MOM is not installed')


class MOMTest(TestCaseBase):

    @testValidation.ValidateRunningAsRoot
    def testKSM(self):
        run = 1
        pages_to_scan = random.randint(100, 200)

        # Set a simple MOM policy to change KSM paramters unconditionally.
        testPolicyStr = """
            (Host.Control "ksm_run" %d)
            (Host.Control "ksm_pages_to_scan" %d)""" % \
            (run, pages_to_scan)
        s = vdscli.connect()
        r = s.setMOMPolicy(testPolicyStr)
        self.assertEqual(r['status']['code'], 0, str(r))

        # Wait for the policy taking effect
        time.sleep(10)

        hostStats = s.getVdsStats()['info']
        self.assertEqual(bool(run), hostStats['ksmState'])
        self.assertEqual(pages_to_scan, hostStats['ksmPages'])
