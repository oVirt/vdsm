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
from __future__ import division

import tempfile

from testlib import VdsmTestCase
from testlib import permutations, expandPermutations

from vdsm import osinfo


@expandPermutations
class TestOsinfo(VdsmTestCase):

    @permutations([
        [b'', ''],
        [b'\n', ''],
        [b'a', 'a'],
        [b'a\n', 'a'],
        [b'a\nb', 'a']
    ])
    def test_kernel_args(self, test_input, expected_result):
        with tempfile.NamedTemporaryFile() as f:
            f.write(test_input)
            f.flush()
            self.assertEqual(osinfo.kernel_args(f.name),
                             expected_result)

    def test_package_versions(self):
        pkgs = osinfo.package_versions()
        self.assertIn('kernel', pkgs)
