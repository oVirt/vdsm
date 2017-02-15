#
# Copyright 2017 Red Hat, Inc.
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

from vdsm.common import exception
from vdsm.common import validate

from testlib import VdsmTestCase as TestCaseBase
from testlib import expandPermutations, permutations


@expandPermutations
class ValidateFunctionsTests(TestCaseBase):

    _PARAMS = {
        'bar': 'foo',
        'fizz': 'buzz',
    }

    @permutations([
        # missing
        [('bar',)],
        [('bar', 'fizz')],
    ])
    def test_require_keys_missing(self, missing):
        params = {k: v for k, v in self._PARAMS.items() if k not in missing}
        self.assertRaises(exception.MissingParameter,
                          validate.require_keys,
                          params,
                          self._PARAMS.keys())

    def test_require_keys_pass(self):
        self.assertNotRaises(validate.require_keys,
                             self._PARAMS,
                             self._PARAMS.keys())
