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
import random
import time
from functools import wraps

import testValidation
from testrunner import VdsmTestCase as TestCaseBase
from nose.plugins.skip import SkipTest
from vdsm import vdscli


def skipNoMOM(method):
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        r = self.s.getVdsCapabilities()
        self.assertEquals(r['status']['code'], 0)
        if not r['info']['packages2'].get('mom'):
            raise SkipTest('MOM is not installed')
        return method(self, *args, **kwargs)
    return wrapped


class MOMTest(TestCaseBase):
    def setUp(self):
        self.s = vdscli.connect()

    @testValidation.ValidateRunningAsRoot
    @skipNoMOM
    def testKSM(self):
        run = 1
        pages_to_scan = random.randint(100, 200)

        # Set a simple MOM policy to change KSM paramters unconditionally.
        testPolicyStr = """
            (Host.Control "ksm_run" %d)
            (Host.Control "ksm_pages_to_scan" %d)""" % \
            (run, pages_to_scan)
        r = self.s.setMOMPolicy(testPolicyStr)
        self.assertEqual(r['status']['code'], 0, str(r))

        # Wait for the policy taking effect
        time.sleep(10)

        hostStats = self.s.getVdsStats()['info']
        self.assertEqual(bool(run), hostStats['ksmState'])
        self.assertEqual(pages_to_scan, hostStats['ksmPages'])
