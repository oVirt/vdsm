# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

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
