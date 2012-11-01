#
# Copyright 2012 Red Hat, Inc.
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

from uuid import uuid4
import threading
import os
import shutil

from testrunner import VdsmTestCase as TestCaseBase

import storage.storage_mailbox as sm
from storage.sd import DOMAIN_META_DATA
from vdsm.utils import retry
import tempfile


class StoragePoolStub(object):
    def __init__(self):
        self.spUUID = str(uuid4())
        self.storage_repository = tempfile.mkdtemp()
        self.__masterDir = os.path.join(self.storage_repository, self.spUUID,
                                        "mastersd", DOMAIN_META_DATA)

        os.makedirs(self.__masterDir)
        for fname in ["id", "inbox", "outbox"]:
            with open(os.path.join(self.__masterDir, fname), "w") as f:
                f.write("DATA")

    def __del__(self):
        # rmtree removes the folder as well
        shutil.rmtree(self.storage_repository)


class SPM_MailMonitorTests(TestCaseBase):
    def testThreadLeak(self):
        mailer = sm.SPM_MailMonitor(StoragePoolStub(), 100)
        threadCount = len(threading.enumerate())
        mailer.stop()
        mailer.run()
        t = lambda: self.assertEquals(threadCount, len(threading.enumerate()))
        retry(AssertionError, t, timeout=4, sleep=0.1)
