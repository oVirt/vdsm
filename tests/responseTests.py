#
# Copyright 2015 Red Hat, Inc.
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

from vdsm import response
from vdsm.define import doneCode
from vdsm.define import errCode

from testlib import permutations, expandPermutations
from testlib import VdsmTestCase as TestCaseBase


@expandPermutations
class ResponseTests(TestCaseBase):

    def test_error(self):
        NAME = 'noVM'  # no special meaning, any error is fine
        res = response.error(NAME)

        template = errCode[NAME]
        self.assertEqual(res["status"]["code"], template["status"]["code"])
        self.assertEqual(res["status"]["message"],
                         template["status"]["message"])

    def test_error_with_message(self):
        NAME = 'noVM'  # no special meaning, any error is fine
        MESSAGE = 'we want a specific message here'
        res = response.error(NAME, MESSAGE)

        template = errCode[NAME]
        self.assertEqual(res["status"]["code"], template["status"]["code"])
        self.assertEqual(res["status"]["message"], MESSAGE)

    def test_success(self):
        res = response.success()

        self.assertEqual(res, {"status": doneCode})

    def test_success_with_message(self):
        MESSAGE = "the message was overwritten"
        res = response.success(message=MESSAGE)

        template = doneCode
        self.assertEqual(res["status"]["code"], template["code"])
        self.assertEqual(res["status"]["message"], MESSAGE)

    def test_success_with_args(self):
        res = response.success(a=1, b=2)

        self.assertEqual(res, {"status": doneCode, "a": 1, "b": 2})

    @permutations([[{'answer': 42}], [{'fooList': ['bar', 'baz']}]])
    def test_success_with_extra_args(self, args):
        res = response.success(**args)
        self.assertEqual(res['status']['code'], 0)
        self.assertEqual(res['status']['message'], 'Done')
        for key in args:
            self.assertEqual(res[key], args[key])
