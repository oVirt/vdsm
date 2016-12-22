#
# Copyright 2012-2016 Red Hat, Inc.
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

import logging
import os
import shutil
import tempfile
import threading

from testlib import make_config
from testlib import VdsmTestCase as TestCaseBase
from monkeypatch import MonkeyPatchScope

from vdsm.utils import retry

from storage.sd import DOMAIN_META_DATA
import storage.storage_mailbox as sm

MAX_HOSTS = 10
MAILER_TIMEOUT = 6
MONITOR_INTERVAL = 0.1


class StoragePoolStub(object):
    def __init__(self):
        self.spUUID = '5d928855-b09b-47a7-b920-bd2d2eb5808c'
        self.storage_repository = tempfile.mkdtemp(dir='/var/tmp')

    def __enter__(self):
        masterdir = os.path.join(
            self.storage_repository, self.spUUID, "mastersd", DOMAIN_META_DATA)

        os.makedirs(masterdir)

        for fname in ["id", "inbox", "outbox"]:
            with open(os.path.join(masterdir, fname), "w") as f:
                f.write(sm.EMPTYMAILBOX * MAX_HOSTS)

        return self

    def __exit__(self, type, value, traceback):
        try:
            shutil.rmtree(self.storage_repository)
        except OSError:
            if type is None:
                raise
            logging.exception("rmtree(%s) failed", self.storage_repository)


class SPM_MailMonitorTests(TestCaseBase):

    def testThreadLeak(self):
        with StoragePoolStub() as pool:
            mailer = sm.SPM_MailMonitor(
                pool, 100, monitorInterval=MONITOR_INTERVAL)
            try:
                threadCount = len(threading.enumerate())
                mailer.stop()
                mailer.run()

                t = lambda: self.assertEquals(
                    threadCount, len(threading.enumerate()))
                retry(AssertionError, t, timeout=4, sleep=0.1)
            finally:
                self.assertTrue(
                    mailer.wait(timeout=MAILER_TIMEOUT),
                    msg='mailer.wait: Timeout expired')


class TestMailbox(TestCaseBase):

    def test_send_receive(self):
        msg_processed = threading.Event()
        expired = False
        received_messages = []

        def spm_callback(msg_id, data):
            received_messages.append((msg_id, data))
            msg_processed.set()

        with StoragePoolStub() as pool:
            config_tweak = make_config([
                ("irs", "repository", pool.storage_repository)])
            with MonkeyPatchScope([(sm, "config", config_tweak)]):
                hsm_mb = sm.HSM_Mailbox(
                    hostID=7, poolID=pool.spUUID,
                    monitorInterval=MONITOR_INTERVAL)
                try:
                    spm_mm = sm.SPM_MailMonitor(
                        pool, MAX_HOSTS, monitorInterval=MONITOR_INTERVAL)
                    try:
                        spm_mm.registerMessageType("xtnd", spm_callback)

                        VOL_DATA = dict(
                            poolID=pool.spUUID,
                            domainID='8adbc85e-e554-4ae0-b318-8a5465fe5fe1',
                            volumeID='d772f1c6-3ebb-43c3-a42e-73fcd8255a5f')
                        REQUESTED_SIZE = 100

                        hsm_mb.sendExtendMsg(VOL_DATA, REQUESTED_SIZE)

                        if not msg_processed.wait(10 * MONITOR_INTERVAL):
                            expired = True
                    finally:
                        spm_mm.stop()
                        self.assertTrue(
                            spm_mm.wait(timeout=MAILER_TIMEOUT),
                            msg='spm_mm.wait: Timeout expired')
                finally:
                    hsm_mb.stop()
                    self.assertTrue(
                        hsm_mb.wait(timeout=MAILER_TIMEOUT),
                        msg='hsm_mb.wait: Timeout expired')

        self.assertFalse(expired, 'message was not processed on time')
        self.assertEqual(received_messages, [(449, (
            "1xtnd\xe1_\xfeeT\x8a\x18\xb3\xe0JT\xe5^\xc8\xdb\x8a_Z%"
            "\xd8\xfcs.\xa4\xc3C\xbb>\xc6\xf1r\xd700000000000000640"
            "0000000000"))])
