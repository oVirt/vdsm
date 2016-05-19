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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#
from __future__ import absolute_import

import os.path
import tempfile

from monkeypatch import MonkeyPatch
from testlib import VdsmTestCase, namedTemporaryDir
from testlib import permutations, expandPermutations

from vdsm import ppc64HardwareInfo


@expandPermutations
class TestHwinfo(VdsmTestCase):

    # TODO: The following tests are testing private functions. We want to avoid
    # that in future. In this case, we have to investigate creation of small
    # module to test device-tree.
    @permutations([
        [b'abc', 'abc'],
        [b'abc\0', 'abc'],
        [b'abc,\0', 'abc'],
        [b'\0abc\n', '\0abc\n'],
    ])
    def test_ppc_device_tree_parsing(self, test_input, expected_result):
        with namedTemporaryDir() as tmpdir:
            with tempfile.NamedTemporaryFile(dir=tmpdir) as f:
                f.write(test_input)
                f.flush()
                result = ppc64HardwareInfo._getFromDeviceTree(
                    os.path.basename(f.name), tree_path=tmpdir)
                self.assertEqual(expected_result, result)

    @MonkeyPatch(os.path, 'exists', lambda _: False)
    def test_ppc_device_tree_no_file(self):
        result = ppc64HardwareInfo._getFromDeviceTree(
            'nonexistent', tree_path='/tmp')
        self.assertEqual('unavailable', result)
