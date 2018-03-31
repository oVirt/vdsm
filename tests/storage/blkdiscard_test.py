#
# Copyright 2016-2017 Red Hat, Inc.
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

from monkeypatch import MonkeyPatch
from testlib import VdsmTestCase

from vdsm.common import cmdutils
from vdsm.storage import blkdiscard


class TestBlkdiscard(VdsmTestCase):

    @MonkeyPatch(blkdiscard._blkdiscard, '_cmd', '/usr/bin/true')
    def test_discard_success(self):
        self.assertNotRaises(blkdiscard.discard, "/dev/vg/lv")

    @MonkeyPatch(blkdiscard._blkdiscard, '_cmd', '/usr/bin/false')
    def test_discard_error(self):
        self.assertRaises(cmdutils.Error, blkdiscard.discard, "/dev/vg/lv")
