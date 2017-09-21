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
import subprocess

from testlib import namedTemporaryDir
from testlib import VdsmTestCase
from testlib import permutations, expandPermutations
from testValidation import broken_on_ci
from testValidation import slowtest

from vdsm import qemuimg
from vdsm import utils
from vdsm.storage import qcow2

MB = 1024 ** 2
GB = 1024 ** 3


@expandPermutations
class TestCountClusters(VdsmTestCase):

    def test_empty(self):
        with namedTemporaryDir() as tmpdir:
            filename = os.path.join(tmpdir, 'test')
            with io.open(filename, "wb"):
                pass
            runs = qemuimg.map(filename)
            self.assertEqual(qcow2.count_clusters(runs), 0)

    @broken_on_ci("File system does not support sparseness", name="TRAVIS_CI")
    def test_empty_sparse(self):
        with namedTemporaryDir() as tmpdir:
            filename = os.path.join(tmpdir, 'test')
            with io.open(filename, "wb") as f:
                f.truncate(MB)
            runs = qemuimg.map(filename)
            self.assertEqual(qcow2.count_clusters(runs), 0)

    def test_full(self):
        with namedTemporaryDir() as tmpdir:
            filename = os.path.join(tmpdir, 'test')
            with io.open(filename, "wb") as f:
                f.write(b"x" * qcow2.CLUSTER_SIZE * 3)
            runs = qemuimg.map(filename)
            self.assertEqual(qcow2.count_clusters(runs), 3)

    def test_multiple_blocks(self):
        with namedTemporaryDir() as tmpdir:
            filename = os.path.join(tmpdir, 'test')
            with io.open(filename, "wb") as f:
                f.write(b"x")
                f.seek(16 * 1024)
                f.write(b"x")
                f.seek(42 * 1024)
                f.write(b"x")
            runs = qemuimg.map(filename)
            self.assertEqual(qcow2.count_clusters(runs), 1)

    @broken_on_ci("File system does not support sparseness", name="TRAVIS_CI")
    def test_partial(self):
        with namedTemporaryDir() as tmpdir:
            filename = os.path.join(tmpdir, 'test')
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
            self.assertEqual(qcow2.count_clusters(runs), 3)

    @broken_on_ci("File system does not support sparseness", name="TRAVIS_CI")
    def test_big_sparse(self):
        with namedTemporaryDir() as tmpdir:
            filename = os.path.join(tmpdir, 'test')
            with io.open(filename, "wb") as f:
                f.truncate(1024 * MB)

                # First cluster
                f.write(b"x")

                # Second cluster
                f.seek(512 * MB)
                f.write(b"x")

            runs = qemuimg.map(filename)
            self.assertEqual(qcow2.count_clusters(runs), 2)


@expandPermutations
class TestAlign(VdsmTestCase):

    @permutations([
        # size, aligned_size
        (0.1, 0),
        (1.1, 8192),
        (1, 8192),
    ])
    def test_align_offset(self, size, aligned_size):
        # qcow2.CLUSTER_SIZE // qcow2.SIZEOF_INT_64 = 8192
        n = 8192
        self.assertEqual(qcow2._align_offset(int(size), n), aligned_size)


@expandPermutations
class TestEstimate(VdsmTestCase):

    @broken_on_ci("File system does not support sparseness", name="TRAVIS_CI")
    @permutations([
        # format, compressed, compat, size
        ("raw", False, '0.10', 1),
        ("qcow2", False, '0.10', 1),
        ("qcow2", True, '0.10', 1),
        ("raw", False, '1.1', 1),
        ("qcow2", False, '1.1', 1),
        ("qcow2", True, '1.1', 1),
    ])
    def test_empty(self, format, compressed, compat, size):
        self.check_empty(format, compressed, compat, size)

    @slowtest
    @broken_on_ci("File system does not support sparseness", name="TRAVIS_CI")
    @permutations([
        # format, compressed, compat, size
        # TODO: tests are slow with qemu 2.6 on rhel,
        # the tests should be merged when we require qemu 2.8
        ("raw", False, '0.10', 10),
        ("qcow2", False, '0.10', 10),
        ("qcow2", True, '0.10', 10),
        ("raw", False, '1.1', 10),
        ("qcow2", False, '1.1', 10),
        ("qcow2", True, '1.1', 10),
        ("raw", False, '0.10', 100),
        ("qcow2", False, '0.10', 100),
        ("qcow2", True, '0.10', 100),
        ("raw", False, '1.1', 100),
        ("qcow2", False, '1.1', 100),
        ("qcow2", True, '1.1', 100),
    ])
    def test_empty_slow(self, format, compressed, compat, size):
        self.check_empty(format, compressed, compat, size)

    def check_empty(self, format, compressed, compat, size):
        with namedTemporaryDir() as tmpdir:
            filename = os.path.join(tmpdir, 'test')
            with io.open(filename, "wb") as f:
                f.truncate(size * GB)
            self.check_estimate(filename, compat, format, compressed)

    @broken_on_ci("File system does not support sparseness", name="TRAVIS_CI")
    @permutations([
        # format, compressed, compat, size
        ("raw", False, '0.10', 1),
        ("qcow2", False, '0.10', 1),
        ("qcow2", True, '0.10', 1),
        ("raw", False, '1.1', 1),
        ("qcow2", False, '1.1', 1),
        ("qcow2", True, '1.1', 1),
    ])
    def test_best(self, format, compressed, compat, size):
        self.check_best_small(format, compressed, compat, size)

    @slowtest
    @broken_on_ci("File system does not support sparseness", name="TRAVIS_CI")
    @permutations([
        ("raw", False, '0.10', 10),
        ("qcow2", False, '0.10', 10),
        ("qcow2", True, '0.10', 10),
        ("raw", False, '1.1', 10),
        ("qcow2", False, '1.1', 10),
        ("qcow2", True, '1.1', 10),
        ("raw", False, '0.10', 100),
        ("qcow2", False, '0.10', 100),
        ("qcow2", True, '0.10', 100),
        ("raw", False, '1.1', 100),
        ("qcow2", False, '1.1', 100),
        ("qcow2", True, '1.1', 100),
    ])
    def test_best_slow(self, format, compressed, compat, size):
        self.check_best_small(format, compressed, compat, size)

    def check_best_small(self, format, compressed, compat, size):
        with namedTemporaryDir() as tmpdir:
            filename = os.path.join(tmpdir, 'test')
            with io.open(filename, "wb") as f:
                f.truncate(size * GB)
                f.write(b"x" * MB)
            self.check_estimate(filename, compat, format, compressed)

    @broken_on_ci("File system does not support sparseness", name="TRAVIS_CI")
    @permutations([
        # format, compressed, compat, size
        ("raw", False, '0.10', 1),
        ("qcow2", False, '0.10', 1),
        ("qcow2", True, '0.10', 1),
        ("raw", False, '1.1', 1),
        ("qcow2", False, '1.1', 1),
        ("qcow2", True, '1.1', 1),
    ])
    def test_big(self, format, compressed, compat, size):
        self.check_best_big(format, compressed, compat, size)

    @slowtest
    @broken_on_ci("File system does not support sparseness", name="TRAVIS_CI")
    @permutations([
        ("raw", False, '0.10', 10),
        ("qcow2", False, '0.10', 10),
        ("qcow2", True, '0.10', 10),
        ("raw", False, '1.1', 10),
        ("qcow2", False, '1.1', 10),
        ("qcow2", True, '1.1', 10),
        ("raw", False, '0.10', 100),
        ("qcow2", False, '0.10', 100),
        ("qcow2", True, '0.10', 100),
        ("raw", False, '1.1', 100),
        ("qcow2", False, '1.1', 100),
        ("qcow2", True, '1.1', 100),
    ])
    def test_big_slow(self, format, compressed, compat, size):
        self.check_best_big(format, compressed, compat, size)

    def check_best_big(self, format, compressed, compat, size):
        with namedTemporaryDir() as tmpdir:
            filename = os.path.join(tmpdir, 'test')
            with io.open(filename, "wb") as f:
                f.truncate(size * GB)
                f.write(b"x" * MB)
                f.seek(512 * MB)
                f.write(b"x" * MB)
            self.check_estimate(filename, compat, format, compressed)

    @slowtest
    @broken_on_ci("File system does not support sparseness", name="TRAVIS_CI")
    @permutations([
        # format, compressed, compat, size
        ("raw", False, '0.10', 1),
        ("qcow2", False, '0.10', 1),
        ("qcow2", True, '0.10', 1),
        ("raw", False, '1.1', 1),
        ("qcow2", False, '1.1', 1),
        ("qcow2", True, '1.1', 1),
    ])
    def test_worst(self, format, compressed, compat, size):
        with namedTemporaryDir() as tmpdir:
            filename = os.path.join(tmpdir, 'test')
            with io.open(filename, "wb") as f:
                f.truncate(size * GB)
                for off in range(qcow2.CLUSTER_SIZE - 1,
                                 1024 * MB,
                                 qcow2.CLUSTER_SIZE):
                    f.seek(off)
                    f.write(b"x")
            self.check_estimate(filename, compat, format, compressed)

    @slowtest
    @broken_on_ci("File system does not support sparseness", name="TRAVIS_CI")
    @permutations([
        # format, compressed, compat, size
        ("raw", False, '0.10', 1),
        ("qcow2", False, '0.10', 1),
        ("qcow2", True, '0.10', 1),
        ("raw", False, '1.1', 1),
        ("qcow2", False, '1.1', 1),
        ("qcow2", True, '1.1', 1),
    ])
    def test_full(self, format, compressed, compat, size):
        with namedTemporaryDir() as tmpdir:
            filename = os.path.join(tmpdir, 'test')
            with io.open(filename, "wb") as f:
                f.truncate(size * GB)
                for _ in range(1024):
                    f.write(b"x" * MB)
            self.check_estimate(filename, compat, format, compressed)

    def check_estimate(self, filename, compat, format, compressed):
        if format != "raw":
            filename = convert_to_qcow2(filename, compressed=compressed,
                                        compat=compat)
        virtual_size = qemuimg.info(filename)["virtualsize"]
        start = utils.monotonic_time()
        estimate = qcow2.estimate_size(filename)
        estimate_time = utils.monotonic_time() - start
        start = utils.monotonic_time()
        actual = converted_size(filename, compat)
        convert_time = utils.monotonic_time() - start
        error_pct = 100 * float(estimate - actual) / virtual_size
        print('estimate=%d, '
              'actual=%s, '
              'error_pct=%.2f%%, '
              'estimate_time=%.2f, '
              'convert_time=%.2f'
              % (estimate, actual, error_pct, estimate_time, convert_time),
              end=" ")
        self.assertGreaterEqual(estimate, actual)
        self.assertLessEqual(error_pct, 0.1)


def converted_size(filename, compat):
    converted = convert_to_qcow2(filename, compat=compat)
    return os.stat(converted).st_size


def convert_to_qcow2(src, compressed=False, compat="1.1"):
    dst = src + ".qcow2"
    cmd = [
        "qemu-img",
        "convert",
        src,
        "-O", "qcow2",
        "-o", "compat=" + compat,
    ]
    if compressed:
        cmd.append("-c")
    cmd.append(dst)
    subprocess.check_call(cmd)
    os.remove(src)
    return dst
