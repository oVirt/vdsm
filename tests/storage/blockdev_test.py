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

from __future__ import absolute_import
from __future__ import division

import io
import os

from contextlib import contextmanager

import pytest

from vdsm.common import exception
from vdsm.storage import blockdev
from vdsm.storage import constants as sc
from vdsm.storage import directio
from vdsm.storage import exception as se

import loopback

# Zeroing and discarding block device is instant, so we can use real lv size.
FILE_SIZE = 128 * 1024**2

# Read and write 128k chunks to keep the tests fast.
DATA = b"x" * 128 * 1024
ZERO = b"\0" * 128 * 1024


@pytest.fixture
def loop_device(tmpdir):
    backing_file = str(tmpdir.join("backing_file"))
    with io.open(backing_file, "wb") as f:
        f.truncate(FILE_SIZE)
    with loopback.Device(backing_file) as loop_device:
        yield loop_device


class TestZero:

    @pytest.mark.skipif(os.geteuid() != 0, reason="requires root")
    def test_entire_device(self, loop_device):
        # Write some data to the device.
        with directio.DirectFile(loop_device.path, "r+") as f:
            f.write(DATA)
            f.seek(FILE_SIZE - len(DATA))
            f.write(DATA)

        # Zero the entire device
        blockdev.zero(loop_device.path)

        # Verify that parts with data were zeroed.
        with directio.DirectFile(loop_device.path, "r") as f:
            data = f.read(len(ZERO))
            assert data == ZERO
            f.seek(FILE_SIZE - len(ZERO))
            data = f.read(len(ZERO))
            assert data == ZERO

    @pytest.mark.skipif(os.geteuid() != 0, reason="requires root")
    def test_size(self, loop_device):
        # Write some data to the device.
        with directio.DirectFile(loop_device.path, "r+") as f:
            f.write(DATA * 2)

        # Zero bytes at the start of the device.
        blockdev.zero(loop_device.path, size=len(ZERO))

        # Verify that the area was zeroed.
        with directio.DirectFile(loop_device.path, "r") as f:
            data = f.read(len(ZERO))
            assert data == ZERO
            data = f.read(len(DATA))
            assert data == DATA

    @pytest.mark.skipif(os.geteuid() != 0, reason="requires root")
    @pytest.mark.parametrize("size", [sc.BLOCK_SIZE, 250 * 4096])
    def test_special_volumes(self, size, loop_device):
        # Write some data to the device.
        with directio.DirectFile(loop_device.path, "r+") as f:
            f.write(b"x" * size * 2)

        # Zero size bytes
        blockdev.zero(loop_device.path, size=size)

        # Verify that size bytes were zeroed and the rest not modified.
        with directio.DirectFile(loop_device.path, "r") as f:
            data = f.read(size)
            assert data == b"\0" * size
            data = f.read(size)
            assert data == b"x" * size

    def test_abort(self, tmpdir):
        # Prepare device poisoned with "x"
        path = str(tmpdir.join("file"))
        with io.open(path, "wb") as f:
            f.write(b"x" * FILE_SIZE)

        # Create a task that will be aborted immediately
        task = AbortingTask()

        # The operation should be stopped
        with pytest.raises(exception.ActionStopped):
            blockdev.zero(path, size=FILE_SIZE, task=task)

        # And the file should not be zeroed
        with io.open(path, "rb") as f:
            data = f.read(FILE_SIZE)
            assert data == b"x" * FILE_SIZE

    @pytest.mark.parametrize("size", [1024**2 - 1, 1024**2 + 1])
    def test_unaligned_size(self, size):
        with pytest.raises(se.InvalidParameterException):
            blockdev.zero("/no/such/path", size=size)


class TestDiscard:

    def test_not_supported(self, tmpdir):
        # Prepare file poisoned with "x"
        path = str(tmpdir.join("backing_file"))
        with io.open(path, "wb") as f:
            f.write(DATA)

        # And discard it - this will fail silently.
        blockdev.discard(path)

        # File should not be modified
        with io.open(path, "rb") as f:
            data = f.read(len(DATA))
            assert data == DATA

    @pytest.mark.skipif(os.geteuid() != 0, reason="requires root")
    def test_supported(self, loop_device):
        # Write some data to the device.
        with directio.DirectFile(loop_device.path, "r+") as f:
            f.write(DATA)
            f.seek(FILE_SIZE - len(DATA))
            f.write(DATA)

        # Discard entire device.
        blockdev.discard(loop_device.path)

        # Check that all data was discarded.
        stat = os.stat(loop_device.backing_file)
        assert stat.st_blocks == 0


class AbortingTask(object):

    @contextmanager
    def abort_callback(self, cb):
        # Invoke the abort callback immediately, aborting the operation before
        # it was started.
        cb()
        yield
