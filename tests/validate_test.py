# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

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
                          list(self._PARAMS.keys()))

    def test_require_keys_pass(self):
        self.assertNotRaises(validate.require_keys,
                             self._PARAMS,
                             list(self._PARAMS.keys()))
