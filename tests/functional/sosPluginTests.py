#
# Copyright 2012 IBM Corporation
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

import subprocess
from testlib import VdsmTestCase as TestCaseBase
from testlib import namedTemporaryDir
import testValidation

ARCHIVE_NAME = "sosplugintest"


class SosPluginTest(TestCaseBase):

    @testValidation.ValidateRunningAsRoot
    def testSosPlugin(self):
        with namedTemporaryDir() as tmpDir:
            cmd = ["sosreport", "-o", "vdsm", "--batch",
                   "--name", ARCHIVE_NAME, "--tmp-dir", tmpDir]

            p = subprocess.Popen(cmd, stdout=subprocess.PIPE)
            (stdout, stderr) = p.communicate()
            if p.returncode:
                self.fail("Failed with executed sosreport, return code: %d" %
                          p.returncode)

            # When sos plugin raise exception, sosreport still exit with
            # successful and print exception to stdout. So check the keyword of
            # exception in output.
            index = stdout.find('Traceback (most recent call last):')
            self.assertEquals(index, -1, "sosreport raised an exception")
