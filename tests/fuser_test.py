# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

import os
from tempfile import NamedTemporaryFile
from vdsm.storage import fuser

from testlib import VdsmTestCase


class TestFuser(VdsmTestCase):

    def testSelfExe(self):
        pid = os.getpid()
        with NamedTemporaryFile() as tempFile:
            self.assertIn(pid, fuser.fuser(tempFile.name))
