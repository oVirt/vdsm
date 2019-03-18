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

from testlib import make_config
from testlib import namedTemporaryDir
from . import loopback

from vdsm import constants
from vdsm.common import exception
from vdsm.storage import blockdev
from vdsm.storage import constants as sc
from vdsm.storage import exception as se


DEFAULT_BLOCK_SIZE = blockdev.zero_block_size()
FILE_SIZE = 2 * DEFAULT_BLOCK_SIZE


@pytest.fixture
def loop_device(tmpdir):
    backing_file = str(tmpdir.join("backing_file"))
    with io.open(backing_file, "wb") as f:
        f.write(b"x" * FILE_SIZE)
    with loopback.Device(backing_file) as loop_device:
        yield loop_device


@pytest.fixture(params=["dd", "blkdiscard"])
def zero_method(request, monkeypatch):
    cfg = make_config([('irs', 'zero_method', request.param)])
    monkeypatch.setattr(blockdev, "config", cfg)
    return request.param


class TestZero:

    @pytest.mark.skipif(os.geteuid() != 0, reason="requires root")
    def test_entire_device(self, loop_device, zero_method):
        # Zero the entire device
        blockdev.zero(loop_device.path)
        # Check that it contains zeros
        with io.open(loop_device.backing_file, "rb") as f:
            data = f.read()
            assert data == b"\0" * FILE_SIZE

    @pytest.mark.skipif(os.geteuid() != 0, reason="requires root")
    def test_size(self, loop_device, zero_method):
        # Zero the first DEFAULT_BLOCK_SIZE
        blockdev.zero(loop_device.path, size=DEFAULT_BLOCK_SIZE)
        with io.open(loop_device.backing_file, "rb") as f:
            # Verify that the first DEFAULT_BLOCK_SIZE is zeroed
            data = f.read(DEFAULT_BLOCK_SIZE)
            assert data == b"\0" * DEFAULT_BLOCK_SIZE
            # And the rest was not modified
            data = f.read(DEFAULT_BLOCK_SIZE)
            assert data == b"x" * DEFAULT_BLOCK_SIZE

    @pytest.mark.skipif(os.geteuid() != 0, reason="requires root")
    @pytest.mark.parametrize("size", [
        (sc.BLOCK_SIZE),
        (DEFAULT_BLOCK_SIZE - sc.BLOCK_SIZE),
        (DEFAULT_BLOCK_SIZE + sc.BLOCK_SIZE),
    ])
    def test_special_volumes(self, size, loop_device, zero_method):
        # Zero size bytes
        blockdev.zero(loop_device.path, size=size)
        with io.open(loop_device.backing_file, "rb") as f:
            # Verify that size bytes were zeroed
            data = f.read(size)
            assert data == b"\0" * size

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

    @pytest.mark.parametrize("size", [
        (FILE_SIZE - 1),
        (FILE_SIZE + 1),
    ])
    def test_unaligned_size(self, size):
        with pytest.raises(se.InvalidParameterException):
            blockdev.zero("/no/such/path", size=size)

    @pytest.mark.parametrize("block_size_mb", ["1", "30", "64"])
    def test_zero_block_size_valid(self, block_size_mb, monkeypatch):
        cfg = make_config([('irs', 'zero_block_size_mb', block_size_mb)])
        monkeypatch.setattr(blockdev, "config", cfg)
        block_size = int(block_size_mb) * constants.MEGAB
        assert blockdev.zero_block_size() == block_size

    @pytest.mark.parametrize("block_size_mb", ["0", "5.5", "65", "str", ""])
    def test_zero_block_size_invalid(self, block_size_mb, monkeypatch):
        cfg = make_config([('irs', 'zero_block_size_mb', block_size_mb)])
        monkeypatch.setattr(blockdev, "config", cfg)
        with pytest.raises(exception.InvalidConfiguration):
            blockdev.zero_block_size()


class TestDiscard:

    def test_not_supported(self):
        with namedTemporaryDir() as tmpdir:
            # Prepare file poisoned with "x"
            path = os.path.join(tmpdir, "backing_file")
            with io.open(path, "wb") as f:
                f.write(b"x" * FILE_SIZE)
            # And discard it - this will fail silently.
            blockdev.discard(path)
            # File should not be modified
            with io.open(path, "rb") as f:
                data = f.read(FILE_SIZE)
                assert data == b"x" * FILE_SIZE

    @pytest.mark.skipif(os.geteuid() != 0, reason="requires root")
    def test_supported(self, loop_device):
        blockdev.discard(loop_device.path)
        stat = os.stat(loop_device.backing_file)
        assert stat.st_blocks == 0


class AbortingTask(object):

    @contextmanager
    def abort_callback(self, cb):
        # Invoke the abort callback immediately, aborting the operation before
        # it was started.
        cb()
        yield
