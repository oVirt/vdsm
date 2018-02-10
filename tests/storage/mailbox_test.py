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

import collections
import contextlib
import io
import os
import threading
import struct

from testlib import namedTemporaryDir

import vdsm.storage.mailbox as sm
from vdsm.storage import misc

MAX_HOSTS = 10
MAILER_TIMEOUT = 6
MONITOR_INTERVAL = 0.1
SPUUID = '5d928855-b09b-47a7-b920-bd2d2eb5808c'


Env = collections.namedtuple("Env", "inbox, outbox")


@contextlib.contextmanager
def make_env():
    with namedTemporaryDir() as tmpdir:
        inbox = os.path.join(tmpdir, "inbox")
        outbox = os.path.join(tmpdir, "outbox")
        data = sm.EMPTYMAILBOX * MAX_HOSTS
        for path in (inbox, outbox):
            with io.open(path, "wb") as f:
                f.write(data)
        yield Env(inbox, outbox)


@contextlib.contextmanager
def make_hsm_mailbox(env, host_id):
    mailbox = sm.HSM_Mailbox(
        hostID=host_id,
        poolID=SPUUID,
        inbox=env.outbox,
        outbox=env.inbox,
        monitorInterval=MONITOR_INTERVAL)
    try:
        yield mailbox
    finally:
        mailbox.stop()
        if not mailbox.wait(timeout=MAILER_TIMEOUT):
            raise RuntimeError('Timemout waiting for hsm mailbox')


@contextlib.contextmanager
def make_spm_mailbox(env):
    mailbox = sm.SPM_MailMonitor(
        SPUUID,
        MAX_HOSTS,
        inbox=env.inbox,
        outbox=env.outbox,
        monitorInterval=MONITOR_INTERVAL)
    try:
        yield mailbox
    finally:
        mailbox.stop()
        if not mailbox.wait(timeout=MAILER_TIMEOUT):
            raise RuntimeError('Timemout waiting for spm mailbox')


@contextlib.contextmanager
def xtnd_message(spm_mm, callback):
    spm_mm.registerMessageType("xtnd", callback)
    try:
        yield
    finally:
        spm_mm.unregisterMessageType("xtnd")


class TestSPMMailMonitor:

    def testThreadLeak(self):
        threadCount = len(threading.enumerate())
        with make_env() as env:
            mailer = sm.SPM_MailMonitor(
                SPUUID, 100,
                inbox=env.inbox,
                outbox=env.outbox,
                monitorInterval=MONITOR_INTERVAL)
            try:
                mailer.stop()
            finally:
                assert mailer.wait(timeout=MAILER_TIMEOUT), \
                    'mailer.wait: Timeout expired'
        assert threadCount == len(threading.enumerate())


class TestSPMMailbox:

    def test_clear_outbox(self):
        with make_env() as env:
            with io.open(env.outbox, "wb") as f:
                f.write(b"x" * sm.MAILBOX_SIZE * MAX_HOSTS)
            with make_spm_mailbox(env):
                with io.open(env.outbox, "rb") as f:
                    data = f.read()
                assert data == sm.EMPTYMAILBOX * MAX_HOSTS


class TestHSMMailbox:

    def test_clear_host_outbox(self):
        host_id = 7
        with make_env() as env:
            # Dirty the inbox
            with io.open(env.inbox, "wb") as f:
                f.write(b"x" * sm.MAILBOX_SIZE * MAX_HOSTS)
            with make_hsm_mailbox(env, host_id):
                with io.open(env.inbox, "rb") as f:
                    data = f.read()
                start = host_id * sm.MAILBOX_SIZE
                end = start + sm.MAILBOX_SIZE
                # Host mailbox must be cleared
                assert data[start:end] == sm.EMPTYMAILBOX
                # Other mailboxes must not be modifed
                assert data[:start] == b"x" * start
                assert data[end:] == b"x" * (len(data) - end)

    def test_keep_outbox(self):
        host_id = 7
        with make_env() as env:
            # Dirty the outbox
            dirty_outbox = b"x" * sm.MAILBOX_SIZE * MAX_HOSTS
            with io.open(env.outbox, "wb") as f:
                f.write(dirty_outbox)
            with make_hsm_mailbox(env, host_id):
                with io.open(env.outbox, "rb") as f:
                    data = f.read()
                assert data == dirty_outbox


class TestCommunicate:

    def test_send_receive(self):
        msg_processed = threading.Event()
        expired = False
        received_messages = []

        def spm_callback(msg_id, data):
            received_messages.append((msg_id, data))
            msg_processed.set()

        with make_env() as env:
            with make_hsm_mailbox(env, 7) as hsm_mb:
                with make_spm_mailbox(env) as spm_mm:
                    with xtnd_message(spm_mm, spm_callback):
                        VOL_DATA = dict(
                            poolID=SPUUID,
                            domainID='8adbc85e-e554-4ae0-b318-8a5465fe5fe1',
                            volumeID='d772f1c6-3ebb-43c3-a42e-73fcd8255a5f')
                        REQUESTED_SIZE = 100

                        hsm_mb.sendExtendMsg(VOL_DATA, REQUESTED_SIZE)

                        if not msg_processed.wait(10 * MONITOR_INTERVAL):
                            expired = True

        assert not expired, 'message was not processed on time'
        assert received_messages == [(449, (
            "1xtnd\xe1_\xfeeT\x8a\x18\xb3\xe0JT\xe5^\xc8\xdb\x8a_Z%"
            "\xd8\xfcs.\xa4\xc3C\xbb>\xc6\xf1r\xd700000000000000640"
            "0000000000"))]


class TestValidation:

    def test_empty_mailbox(self):
        mailbox = sm.EMPTYMAILBOX
        assert not sm.SPM_MailMonitor.validateMailbox(mailbox, 7)

    def test_good_checksum(self):
        msg = "x" * sm.MESSAGE_SIZE
        padding = sm.MAILBOX_SIZE - sm.MESSAGE_SIZE - sm.CHECKSUM_BYTES
        data = msg + padding * "\0"
        n = misc.checksum(data, sm.CHECKSUM_BYTES)
        checksum = struct.pack('<l', n)
        mailbox = data + checksum
        assert sm.SPM_MailMonitor.validateMailbox(mailbox, 7)

    def test_bad_checksum(self):
        msg = "x" * sm.MESSAGE_SIZE
        padding = sm.MAILBOX_SIZE - sm.MESSAGE_SIZE - sm.CHECKSUM_BYTES
        data = msg + padding * "\0"
        mailbox = data + "bad!"
        assert not sm.SPM_MailMonitor.validateMailbox(mailbox, 7)
