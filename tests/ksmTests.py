#
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
from testrunner import VdsmTestCase as TestCaseBase
import ksm


class KsmTests(TestCaseBase):
    def testReadProcInexistent(self):
        # Do we deal correctly with not-existent paths?
        self.assertEquals(ksm._readProcFSInt('/proc/inexistent'), 0)

    def testReadProcNotInt(self):
        # what about unexpected content?
        self.assertEquals(ksm._readProcFSInt('/proc/version'), 0)

    def testReadProcValid(self):
        # we need a procfs entry which exists on every system and
        # with a predictable value.
        threadsMax = ksm._readProcFSInt('/proc/sys/kernel/threads-max')
        # assertGreater would be better, but requires python 2.7
        # and python 2.6 is still around.
        self.assertTrue(threadsMax > 0)
