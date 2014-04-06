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

from testrunner import VdsmTestCase as TestCaseBase
import storage.outOfProcess as oop
import os
import tempfile


class OopWrapperTests(TestCaseBase):
    def setUp(self):
        self.pool = oop.getGlobalProcPool()

    def testEcho(self):
        data = """Censorship always defeats it own purpose, for it creates in
                  the end the kind of society that is incapable of exercising
                  real discretion."""
        # Henry Steele Commager

        self.assertEquals(self.pool.echo(data), data)

    def testFileUtilsCall(self):
        """fileUtils is a custom module and calling it might break even though
        built in module calls arn't broken"""
        path = "/dev/null"
        self.assertEquals(self.pool.fileUtils.pathExists(path), True)

    def testSubModuleCall(self):
        path = "/dev/null"
        self.assertEquals(self.pool.os.path.exists(path), True)

    def testModuleCall(self):
        self.assertNotEquals(self.pool.os.getpid(), os.getpid())

    def testUtilsFuncs(self):
        tmpfd, tmpfile = tempfile.mkstemp()
        self.pool.utils.rmFile(tmpfile)
        os.close(tmpfd)
        return True
