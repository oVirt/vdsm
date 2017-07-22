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

from __future__ import print_function

import io
import os

import pytest
import six

from vdsm.common import time
from vdsm.storage import qcow2
from vdsm.storage import qemuimg

MB = 1024 ** 2
GB = 1024 ** 3


class TestCountClusters:

    def test_empty(self, tmpdir):
        filename = str(tmpdir.join("test"))
        with io.open(filename, "wb"):
            pass
        runs = qemuimg.map(filename)
        assert qcow2.count_clusters(runs) == 0

    @pytest.mark.xfail("TRAVIS_CI" in os.environ,
                       reason="File system does not support sparseness")
    def test_empty_sparse(self, tmpdir):
        filename = str(tmpdir.join("test"))
        with io.open(filename, "wb") as f:
            f.truncate(MB)
        runs = qemuimg.map(filename)
        assert qcow2.count_clusters(runs) == 0

    def test_full(self, tmpdir):
        filename = str(tmpdir.join("test"))
        with io.open(filename, "wb") as f:
            f.write(b"x" * qcow2.CLUSTER_SIZE * 3)
        runs = qemuimg.map(filename)
        assert qcow2.count_clusters(runs) == 3

    def test_multiple_blocks(self, tmpdir):
        filename = str(tmpdir.join("test"))
        with io.open(filename, "wb") as f:
            f.write(b"x")
            f.seek(16 * 1024)
            f.write(b"x")
            f.seek(42 * 1024)
            f.write(b"x")
        runs = qemuimg.map(filename)
        assert qcow2.count_clusters(runs) == 1

    @pytest.mark.xfail("TRAVIS_CI" in os.environ,
                       reason="File system does not support sparseness")
    def test_partial(self, tmpdir):
        filename = str(tmpdir.join("test"))
        with io.open(filename, "wb") as f:
            f.truncate(MB)

            # First cluster
            f.seek(8192)
            f.write(b"x")

            # Second cluster
            f.seek(qcow2.CLUSTER_SIZE)
            f.write(b"x")
            f.seek(qcow2.CLUSTER_SIZE * 2 - 1)
            f.write(b"x")

            # Third cluster
            f.seek(qcow2.CLUSTER_SIZE * 2)
            f.write(b"x")

        runs = qemuimg.map(filename)
        assert qcow2.count_clusters(runs) == 3

    @pytest.mark.xfail("TRAVIS_CI" in os.environ,
                       reason="File system does not support sparseness")
    def test_big_sparse(self, tmpdir):
        filename = str(tmpdir.join("test"))
        with io.open(filename, "wb") as f:
            f.truncate(1024 * MB)

            # First cluster
            f.write(b"x")

            # Second cluster
            f.seek(512 * MB)
            f.write(b"x")

        runs = qemuimg.map(filename)
        assert qcow2.count_clusters(runs) == 2


class TestAlign:

    @pytest.mark.parametrize("size,aligned_size", [
        (0.1, 0),
        (1.1, 8192),
        (1, 8192),
    ])
    def test_align_offset(self, size, aligned_size):
        # qcow2.CLUSTER_SIZE // qcow2.SIZEOF_INT_64 = 8192
        n = 8192
        assert qcow2._align_offset(int(size), n) == aligned_size


@pytest.mark.xfail(six.PY3,
                   reason="qemuimg.ProgressCommand mixes strings and bytes")
class TestEstimate:

    @pytest.mark.xfail("TRAVIS_CI" in os.environ,
                       reason="File system does not support sparseness")
    @pytest.mark.parametrize("compat,size", [
        ('0.10', 1),
        ('1.1', 1),
        # TODO: tests are slow with qemu 2.6 on rhel,
        # the tests should be merged when we require qemu 2.8
        pytest.param('0.10', 10, marks=pytest.mark.slow),
        pytest.param('1.1', 10, marks=pytest.mark.slow),
        pytest.param('0.10', 100, marks=pytest.mark.slow),
        pytest.param('1.1', 100, marks=pytest.mark.slow),
    ])
    def test_empty(self, tmpdir, compat, size):
        filename = str(tmpdir.join("test"))
        with io.open(filename, "wb") as f:
            f.truncate(size * GB)
        self.check_estimate(filename, compat)

    @pytest.mark.xfail("TRAVIS_CI" in os.environ,
                       reason="File system does not support sparseness")
    @pytest.mark.parametrize("compat,size", [
        ('0.10', 1),
        ('1.1', 1),
        # TODO: tests are slow with qemu 2.6 on rhel,
        # the tests should be merged when we require qemu 2.8
        pytest.param('0.10', 10, marks=pytest.mark.slow),
        pytest.param('1.1', 10, marks=pytest.mark.slow),
        pytest.param('0.10', 100, marks=pytest.mark.slow),
        pytest.param('1.1', 100, marks=pytest.mark.slow),
    ])
    def test_best_small(self, tmpdir, compat, size):
        filename = str(tmpdir.join("test"))
        with io.open(filename, "wb") as f:
            f.truncate(size * GB)
            f.write(b"x" * MB)
        self.check_estimate(filename, compat)

    @pytest.mark.xfail("TRAVIS_CI" in os.environ,
                       reason="File system does not support sparseness")
    @pytest.mark.parametrize("compat,size", [
        ('0.10', 1),
        ('1.1', 1),
        # TODO: tests are slow with qemu 2.6 on rhel,
        # the tests should be merged when we require qemu 2.8
        pytest.param('0.10', 10, marks=pytest.mark.slow),
        pytest.param('1.1', 10, marks=pytest.mark.slow),
        pytest.param('0.10', 100, marks=pytest.mark.slow),
        pytest.param('1.1', 100, marks=pytest.mark.slow),
    ])
    def test_big(self, tmpdir, compat, size):
        filename = str(tmpdir.join("test"))
        with io.open(filename, "wb") as f:
            f.truncate(size * GB)
            f.write(b"x" * MB)
            f.seek(512 * MB)
            f.write(b"x" * MB)
        self.check_estimate(filename, compat)

    @pytest.mark.slow
    @pytest.mark.parametrize("compat,size", [
        ('0.10', 1),
        ('1.1', 1),
    ])
    def test_worst(self, tmpdir, compat, size):
        filename = str(tmpdir.join("test"))
        with io.open(filename, "wb") as f:
            f.truncate(size * GB)
            for off in range(qcow2.CLUSTER_SIZE - 1,
                             1024 * MB,
                             qcow2.CLUSTER_SIZE):
                f.seek(off)
                f.write(b"x")
        self.check_estimate(filename, compat)

    @pytest.mark.slow
    @pytest.mark.parametrize("compat,size", [
        ('0.10', 1),
        ('1.1', 1),
    ])
    def test_full(self, tmpdir, compat, size):
        filename = str(tmpdir.join("test"))
        with io.open(filename, "wb") as f:
            f.truncate(size * GB)
            for _ in range(1024):
                f.write(b"x" * MB)
        self.check_estimate(filename, compat)

    def check_estimate(self, filename, compat):
        start = time.monotonic_time()
        estimate = qcow2.estimate_size(filename)
        estimate_time = time.monotonic_time() - start
        start = time.monotonic_time()
        actual = converted_size(filename, compat)
        convert_time = time.monotonic_time() - start
        original_size = os.stat(filename).st_size
        error_pct = 100 * float(estimate - actual) / original_size
        print('estimate=%d, '
              'actual=%s, '
              'error_pct=%.2f%%, '
              'estimate_time=%.2f, '
              'convert_time=%.2f'
              % (estimate, actual, error_pct, estimate_time, convert_time),
              end=" ")
        assert estimate >= actual
        assert error_pct <= 0.1, error_pct


def converted_size(filename, compat):
    converted = filename + ".qcow2"
    operation = qemuimg.convert(filename,
                                converted,
                                srcFormat=qemuimg.FORMAT.RAW,
                                dstFormat=qemuimg.FORMAT.QCOW2,
                                dstQcow2Compat=compat)
    operation.run()
    return os.stat(converted).st_size
