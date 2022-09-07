# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

import os.path

from vdsm.host import rngsources

from monkeypatch import MonkeyPatchScope
from testlib import permutations, expandPermutations
from testlib import VdsmTestCase as TestCaseBase


@expandPermutations
class TestRng(TestCaseBase):

    @permutations([
        # available_sources_map, output_sources
        [{'/dev/random': True, '/dev/hwrng': True, '/dev/urandom': True},
         ['random', 'hwrng']],
        [{'/dev/random': True, '/dev/hwrng': True}, ['random', 'hwrng']],
        [{'/dev/random': True, '/dev/hwrng': False}, ['random']],
        [{'/dev/random': False, '/dev/hwrng': True}, ['hwrng']],
        [{'/dev/random': False, '/dev/hwrng': False}, []],
    ])
    def test_list_available(self, available_sources_map, output_sources):

        def fake_path_exists(path):
            return available_sources_map.get(path, False)

        with MonkeyPatchScope([(os.path, 'exists', fake_path_exists)]):
            available = list(sorted(rngsources.list_available()))

        expected = list(sorted(output_sources))
        self.assertEqual(available, expected)
