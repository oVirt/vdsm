#
# Copyright 2016 Red Hat, Inc.
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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#
from __future__ import absolute_import

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
