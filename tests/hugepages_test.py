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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA
# 02110-1301  USA
#
# Refer to the README and COPYING files for full details of the license
#

import os
import tempfile

from monkeypatch import MonkeyPatch
from testlib import VdsmTestCase as TestCaseBase
from testlib import mock
from testlib import namedTemporaryDir
from testlib import permutations, expandPermutations

from vdsm import hugepages
from vdsm import supervdsm
from vdsm.supervdsm_api import virt


_STATE = {
    'resv_hugepages': '1234',
    'free_hugepages': '1234',
    'nr_overcommit_hugepages': '1234',
    'surplus_hugepages': '1234',
    'nr_hugepages': '1234',
    'nr_hugepages_mempolicy': '1234'
}


@expandPermutations
class TestHugepages(TestCaseBase):

    @permutations([
        ['1024', 1024, 1024],
        ['1024', -1024, -1024],
        ['1024', -512, -512],
        ['1024', 0, 0],
    ])
    @MonkeyPatch(hugepages, '_size_from_dir', lambda x: x)
    @MonkeyPatch(hugepages, 'state', lambda: {2048: _STATE})
    @MonkeyPatch(supervdsm, 'getProxy', lambda: virt)
    def test_alloc(self, default, count, expected):
        with tempfile.NamedTemporaryFile() as f:
            f.write(default)
            f.flush()
            ret = hugepages._alloc(count, size=2048, path=f.name)
            f.seek(0)
            self.assertEqual(ret, expected)

    @MonkeyPatch(hugepages, '_size_from_dir', lambda x: x)
    def test_supported(self):
        with namedTemporaryDir() as src:
            # A list of 3 file names, where the files are temporary.
            sizes = [os.path.basename(f.name) for f in [
                tempfile.NamedTemporaryFile(
                    dir=src, delete=False
                ) for _ in range(3)
            ]]
            with mock.patch('{}.open'.format(hugepages.__name__),
                            mock.mock_open(read_data=''),
                            create=True):

                self.assertEqual(set(hugepages.supported(src)), set(sizes))

    @MonkeyPatch(hugepages, '_size_from_dir', lambda x: x)
    def test_state(self):
        with namedTemporaryDir() as src:
            # A list of 3 file names, where the files are temporary.
            sizes = [os.path.basename(f.name) for f in [
                tempfile.NamedTemporaryFile(
                    dir=src, delete=False
                ) for _ in range(3)
            ]]
            with mock.patch('{}.open'.format(hugepages.__name__),
                            mock.mock_open(read_data='1234'),
                            create=True):

                self.assertEqual(len(hugepages.state(src)), len(sizes))
                for value in hugepages.state(src).values():
                    self.assertEqual(value, _STATE)

    @permutations([
        ['hugepages-2048Kb', 2048],
        ['hugepages-10000Kb', 10000],
        ['hugepages-1Kb', 1],
    ])
    def test_size_from_dir(self, filename, expected):
        self.assertEqual(hugepages._size_from_dir(filename), expected)
