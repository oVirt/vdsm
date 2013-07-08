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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA
# 02110-1301  USA
#
# Refer to the README and COPYING files for full details of the license
#

from testrunner import VdsmTestCase as TestCaseBase

import storage.lvm as lvm


class LvmTests(TestCaseBase):
    def test_buildFilter(self):
        chars = [' ', '$', '|', '"', '(']
        dev = "/dev/mapper/a"
        signedDev = dev
        for c in chars:
            signedDev += '\\' + hex(ord(c))[1:4]
        devs = [signedDev]
        filter = lvm._buildFilter(devs)
        expectedFilter = ("filter = [ \'a|" + dev + "\\\\x20\\\\x24\\\\x7c"
                          "\\\\x22\\\\x28|\', \'r|.*|\' ]"
                          )
        self.assertEqual(expectedFilter, filter)
