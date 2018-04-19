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

from monkeypatch import MonkeyPatch
from testlib import VdsmTestCase
from testlib import temporaryPath
from testlib import namedTemporaryDir
from vdsm.common import cmdutils
from vdsm.storage import fallocate


class TestFallocate(VdsmTestCase):

    @MonkeyPatch(fallocate, '_FALLOCATE', '../helpers/fallocate')
    def test_allocate(self):
        # Test that allocate call made correctly
        size = 4096
        with namedTemporaryDir() as tmpdir:
            image = os.path.join(tmpdir, "image")

            fallocate.allocate(image, size).run()

            allocated = os.stat(image).st_blocks * 512
            self.assertEqual(allocated, size)

    @MonkeyPatch(fallocate, '_FALLOCATE', '../helpers/fallocate')
    def test_negative_size(self):
        # Test that fallocate call throws exception on error
        with namedTemporaryDir() as tmpdir:
            image = os.path.join(tmpdir, "image")
            with self.assertRaises(cmdutils.Error):
                fallocate.allocate(image, -1).run()

    @MonkeyPatch(fallocate, '_FALLOCATE', '../helpers/fallocate')
    def test_zero_size(self):
        # Test that fallocate call throws exception on error
        with namedTemporaryDir() as tmpdir:
            image = os.path.join(tmpdir, "image")
            with self.assertRaises(cmdutils.Error):
                fallocate.allocate(image, 0).run()

    @MonkeyPatch(fallocate, '_FALLOCATE', '../helpers/fallocate')
    def test_resize(self):
        # Test that resize call actually works
        size = 4096
        with temporaryPath(data=b'x' * size) as image:
            fallocate.allocate(image, size, offset=size).run()

            with io.open(image, 'rb') as f:
                actual = f.read()

            expected = b'x' * size + b'\0' * size

            self.assertEqual(expected, actual)

            allocated = os.stat(image).st_blocks * 512
            self.assertEqual(allocated, size * 2)
