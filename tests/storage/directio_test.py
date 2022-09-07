# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

import io

from testlib import VdsmTestCase
from testlib import permutations, expandPermutations
from testlib import temporaryPath

from vdsm.storage import directio

BLOCK_SIZE = 512


@expandPermutations
class TestDirectFile(VdsmTestCase):

    DATA = b"".join(c.encode('ascii') * 127 + b"\n" for c in "abcdefgh")

    @permutations([[i * BLOCK_SIZE] for i in range(4)])
    def test_read(self, size):
        with temporaryPath(data=self.DATA) as srcPath, \
                directio.open(srcPath) as f:
            self.assertEqual(f.read(size), self.DATA[:size])

    @permutations([[1 * BLOCK_SIZE], [2 * BLOCK_SIZE]])
    def test_seek_and_read(self, offset):
        with temporaryPath(data=self.DATA) as srcPath, \
                directio.open(srcPath) as f:
            f.seek(offset)
            self.assertEqual(f.read(), self.DATA[offset:])

    def test_write(self):
        with temporaryPath() as srcPath, \
                directio.open(srcPath, "w") as f:
            f.write(self.DATA)
            with io.open(srcPath, "rb") as f:
                self.assertEqual(f.read(), self.DATA)

    def test_small_writes(self):
        with temporaryPath() as srcPath, \
                directio.open(srcPath, "w") as f:
            f.write(self.DATA[:BLOCK_SIZE])
            f.write(self.DATA[BLOCK_SIZE:])

            with io.open(srcPath, "rb") as f:
                self.assertEqual(f.read(), self.DATA)

    def test_write_unaligned(self):
        with temporaryPath(data=self.DATA) as srcPath, \
                directio.open(srcPath, "r+") as f:
            self.assertRaises(ValueError, f.write, "x" * 511)
            with io.open(srcPath, "rb") as f:
                self.assertEqual(f.read(), self.DATA)

    def test_update_and_read(self):
        with temporaryPath() as srcPath, \
                directio.open(srcPath, "w") as f:
            f.write(self.DATA[:BLOCK_SIZE])

            with directio.open(srcPath, "r+") as f:
                f.seek(BLOCK_SIZE)
                f.write(self.DATA[BLOCK_SIZE:])

            with io.open(srcPath, "rb") as f:
                self.assertEqual(f.read(), self.DATA)

    def test_readlines(self):
        with temporaryPath(data=self.DATA) as srcPath, \
                directio.open(srcPath) as direct_file, \
                io.open(srcPath, "rb") as buffered_file:
            self.assertEqual(direct_file.readlines(),
                             buffered_file.readlines())

    def test_read_all(self):
        with temporaryPath(data=self.DATA) as srcPath, \
                directio.open(srcPath) as direct_file, \
                io.open(srcPath, "rb") as buffered_file:
            self.assertEqual(direct_file.read(), buffered_file.read())
