# Copyright 2015 Red Hat, Inc.
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

from testlib import VdsmTestCase as TestCaseBase
from vdsm.tool.dump_volume_chains import (_build_volume_chain, _BLANK_UUID,
                                          OrphanVolumes, ChainLoopError,
                                          NoBaseVolume, DuplicateParentError)


class GetVolumeChainTests(TestCaseBase):
    def test_empty(self):
        self.assertEqual(_build_volume_chain([]), [])

    def test_only_base_volume(self):
        self.assertEqual(_build_volume_chain([(_BLANK_UUID, 'a')]), ['a'])

    def test_orphan_volumes(self):
        volumes_children = [(_BLANK_UUID, 'a'), ('a', 'b'), ('c', 'd')]
        with self.assertRaises(OrphanVolumes) as cm:
            _build_volume_chain(volumes_children)
        self.assertEqual(cm.exception.volumes_children, volumes_children)

    def test_simple_chain(self):
        volumes_children = [(_BLANK_UUID, 'a'), ('a', 'b'), ('b', 'c')]
        self.assertEqual(_build_volume_chain(
            volumes_children), ['a', 'b', 'c'])

    def test_loop(self):
        with self.assertRaises(ChainLoopError):
            _build_volume_chain([
                (_BLANK_UUID, 'a'), ('a', 'b'), ('b', 'c'), ('c', 'a')])

    def test_no_base_volume(self):
        with self.assertRaises(NoBaseVolume):
            _build_volume_chain([('a', 'b'), ('b', 'c')])

    def test_duplicate_parent(self):
        with self.assertRaises(DuplicateParentError):
            _build_volume_chain(
                [(_BLANK_UUID, 'a'), ('a', 'b'), ('a', 'c')])
