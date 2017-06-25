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

import io
import os

from contextlib import contextmanager

import pytest

from testlib import VdsmTestCase
from testlib import expandPermutations, permutations
from testlib import namedTemporaryDir
import loopback

from vdsm.common import exception
from vdsm.storage import blockdev
from vdsm.storage import constants as sc
from vdsm.storage import exception as se

SIZE = blockdev.OPTIMAL_BLOCK_SIZE


@expandPermutations
class TestZero(VdsmTestCase):

    def test_entire_device(self):
        with namedTemporaryDir() as tmpdir:
            # Prepare device poisoned with "x"
            path = os.path.join(tmpdir, "file")
            with io.open(path, "wb") as f:
                f.write(b"x" * SIZE)
            # Zero the entire device
            blockdev.zero(path)
            # Check that it contains zeros
            with io.open(path, "rb") as f:
                data = f.read()
                self.assertEqual(data, b"\0" * SIZE, "data was not zeroed")

    def test_size(self):
        with namedTemporaryDir() as tmpdir:
            # Prepare device poisoned with "x"
            path = os.path.join(tmpdir, "file")
            with io.open(path, "wb") as f:
                f.write(b"x" * SIZE * 2)
            # Zero the first 1Mib
            blockdev.zero(path, size=SIZE)
            with io.open(path, "rb") as f:
                # Verify that the first 1MiB is zeroed
                data = f.read(SIZE)
                self.assertEqual(data, b"\0" * SIZE, "data was not zeroed")
                # And the rest was not modified
                data = f.read(SIZE)
                self.assertEqual(data, b"x" * SIZE, "data was modified")

    @permutations([
        (sc.BLOCK_SIZE,),
        (blockdev.OPTIMAL_BLOCK_SIZE - sc.BLOCK_SIZE,),
        (blockdev.OPTIMAL_BLOCK_SIZE + sc.BLOCK_SIZE,),
    ])
    def test_special_volumes(self, size):
        with namedTemporaryDir() as tmpdir:
            # Prepare device posioned with "x"
            path = os.path.join(tmpdir, "file")
            with io.open(path, "wb") as f:
                f.write(b"x" * size)
            # Zero size bytes
            blockdev.zero(path, size=size)
            with io.open(path, "rb") as f:
                # Verify that size bytes were zeroed
                data = f.read(size)
                self.assertEqual(data, b"\0" * size, "data was not zeroed")

    def test_abort(self):
        with namedTemporaryDir() as tmpdir:
            # Prepare device poisoned with "x"
            path = os.path.join(tmpdir, "file")
            with io.open(path, "wb") as f:
                f.write(b"x" * SIZE)
            # Create a task that will be aborted immediately
            task = AbortingTask()
            # The operation should be stopped
            with self.assertRaises(exception.ActionStopped):
                blockdev.zero(path, size=SIZE, task=task)
            # And the file should not be zeroed
            with io.open(path, "rb") as f:
                data = f.read(SIZE)
                self.assertEqual(data, b"x" * SIZE, "data was modified")

    @permutations([(SIZE - 1,), (SIZE + 1,)])
    def test_unaligned_size(self, size):
        with self.assertRaises(se.InvalidParameterException):
            blockdev.zero("/no/such/path", size=size)


class TestDiscard(VdsmTestCase):

    def test_not_supported(self):
        with namedTemporaryDir() as tmpdir:
            # Prepare file poisoned with "x"
            path = os.path.join(tmpdir, "backing_file")
            with io.open(path, "wb") as f:
                f.write(b"x" * SIZE)
            # And discard it - this will fail silently.
            blockdev.discard(path)
            # File should not be modified
            with io.open(path, "rb") as f:
                data = f.read(SIZE)
                self.assertEqual(data, b"x" * SIZE, "data was modified")

    @pytest.mark.skipif(os.geteuid() != 0, reason="requires root")
    def test_supported(self):
        with namedTemporaryDir() as tmpdir:
            # Prepare backing file poisoned with "x"
            backing_file = os.path.join(tmpdir, "backing_file")
            with io.open(backing_file, "wb") as f:
                f.write(b"x" * SIZE)
            # Create a loop device
            with loopback.Device(backing_file) as loop_device:
                # And discard it
                blockdev.discard(loop_device.path)
                stat = os.stat(backing_file)
                self.assertEqual(stat.st_blocks, 0)


class AbortingTask(object):

    @contextmanager
    def abort_callback(self, cb):
        # Invoke the abort callback immediately, aborting the operation before
        # it was started.
        cb()
        yield
