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

from __future__ import absolute_import
from __future__ import division

import collections
import contextlib
import io
import logging
import random
import struct
import threading
import time
import uuid

from functools import partial

import pytest

from testlib import make_uuid
from testlib import make_config

import vdsm.storage.mailbox as sm

from vdsm.common.units import MiB, GiB

MAX_HOSTS = 10
MAILER_TIMEOUT = 10

# Use shorter intervals for quicker tests.
MONITOR_INTERVAL = 0.4
EVENT_INTERVAL = 0.1

SPUUID = '5d928855-b09b-47a7-b920-bd2d2eb5808c'


MboxFiles = collections.namedtuple("MboxFiles", "inbox, outbox")

log = logging.getLogger("test")


def volume_data(volume_id=None):
    if volume_id is None:
        volume_id = 'd772f1c6-3ebb-43c3-a42e-73fcd8255a5f'
    return dict(poolID=SPUUID,
                domainID='8adbc85e-e554-4ae0-b318-8a5465fe5fe1',
                volumeID=volume_id)


def extend_message(size=128 * MiB):
    # Generates a 64 bytes long extend message, with extend size given
    # as parameter. The message volume data is the default result of
    # volume_data().
    message = (
        b"\x31\x78\x74\x6e\x64\xe1\x5f\xfe"
        b"\x65\x54\x8a\x18\xb3\xe0\x4a\x54"
        b"\xe5\x5e\xc8\xdb\x8a\x5f\x5a\x25"
        b"\x25\xd8\xfc\x73\x2e\xa4\xc3\x43"
        b"\xbb\x3e\xc6\xf1\x72\xd7\x30\x30"
        b"\x30\x30\x30\x30\x30\x30%08x\x30"
        b"\x30\x30\x30\x30\x30\x30\x30\x30"
        b"\x30\x30") % size
    return message


@pytest.fixture()
def mboxfiles(tmpdir):
    data = sm.EMPTYMAILBOX * MAX_HOSTS
    inbox = tmpdir.join('inbox')
    outbox = tmpdir.join('outbox')
    inbox.write(data)
    outbox.write(data)
    yield MboxFiles(str(inbox), str(outbox))


def read_mbox(mboxfiles):
    with io.open(mboxfiles.inbox, 'rb') as inf, \
            io.open(mboxfiles.outbox, 'rb') as outf:
        return inf.read(), outf.read()


@contextlib.contextmanager
def make_hsm_mailbox(mboxfiles, host_id):
    mailbox = sm.HSM_Mailbox(
        hostID=host_id,
        poolID=SPUUID,
        inbox=mboxfiles.outbox,
        outbox=mboxfiles.inbox,
        monitorInterval=MONITOR_INTERVAL,
        eventInterval=EVENT_INTERVAL)
    try:
        yield mailbox
    finally:
        mailbox.stop()
        if not mailbox.wait(timeout=MAILER_TIMEOUT):
            raise RuntimeError('Timemout waiting for hsm mailbox')


@contextlib.contextmanager
def make_spm_mailbox(mboxfiles):
    mailbox = sm.SPM_MailMonitor(
        SPUUID,
        MAX_HOSTS,
        inbox=mboxfiles.inbox,
        outbox=mboxfiles.outbox,
        monitorInterval=MONITOR_INTERVAL,
        eventInterval=EVENT_INTERVAL)
    mailbox.start()
    try:
        yield mailbox
    finally:
        mailbox.stop()
        if not mailbox.wait(timeout=MAILER_TIMEOUT):
            raise RuntimeError('Timemout waiting for spm mailbox')


class FakeSPMMailer(object):
    """
    Fake SPM mailer class for sending reply message when
    pool extend volume request handling is done.
    """
    def __init__(self):
        self.msg_id = None
        self.msg = None

    def sendReply(self, msg_id, msg):
        self.msg_id = msg_id
        self.msg = msg


class FakePool(object):
    """
    Fake storage pool class implementing the extend volume interface used by
    storage mailbox code.
    """
    spUUID = SPUUID

    def __init__(self, mailer):
        self.spmMailer = mailer
        self.volume_data = None

    def extendVolume(self, sdUUID, volUUID, newSize):
        self.volume_data = {
            'domainID': sdUUID,
            'volumeID': volUUID,
            'size': newSize
        }


class TestSPMMailMonitor:

    def test_thread_leak(self, mboxfiles):
        thread_count = len(threading.enumerate())
        mailer = sm.SPM_MailMonitor(
            SPUUID, 100,
            inbox=mboxfiles.inbox,
            outbox=mboxfiles.outbox,
            monitorInterval=MONITOR_INTERVAL,
            eventInterval=EVENT_INTERVAL)
        mailer.start()
        try:
            mailer.stop()
        finally:
            assert mailer.wait(timeout=MAILER_TIMEOUT), \
                'mailer.wait: Timeout expired'
        assert thread_count == len(threading.enumerate())

    def test_clear_outbox(self, mboxfiles):
        with io.open(mboxfiles.outbox, "wb") as f:
            f.write(b"x" * sm.MAILBOX_SIZE * MAX_HOSTS)
        with make_spm_mailbox(mboxfiles):
            with io.open(mboxfiles.outbox, "rb") as f:
                data = f.read()
            assert data == sm.EMPTYMAILBOX * MAX_HOSTS

    def test_skip_empty_request(self, mboxfiles, monkeypatch):
        with make_spm_mailbox(mboxfiles) as spm_mm:
            assert not spm_mm._handleRequests(sm.EMPTYMAILBOX * MAX_HOSTS)


class TestHSMMailbox:

    def test_clear_host_outbox(self, mboxfiles):
        host_id = 7

        # Dirty the inbox
        with io.open(mboxfiles.inbox, "wb") as f:
            f.write(b"x" * sm.MAILBOX_SIZE * MAX_HOSTS)
        with make_hsm_mailbox(mboxfiles, host_id):
            with io.open(mboxfiles.inbox, "rb") as f:
                data = f.read()
            start = host_id * sm.MAILBOX_SIZE
            end = start + sm.MAILBOX_SIZE
            # Host mailbox must be cleared
            assert data[start:end] == sm.EMPTYMAILBOX
            # Other mailboxes must not be modifed
            assert data[:start] == b"x" * start
            assert data[end:] == b"x" * (len(data) - end)

    def test_keep_outbox(self, mboxfiles):
        host_id = 7

        # Dirty the outbox
        dirty_outbox = b"x" * sm.MAILBOX_SIZE * MAX_HOSTS
        with io.open(mboxfiles.outbox, "wb") as f:
            f.write(dirty_outbox)
        with make_hsm_mailbox(mboxfiles, host_id):
            with io.open(mboxfiles.outbox, "rb") as f:
                data = f.read()
            assert data == dirty_outbox

    def test_skip_empty_response(self, mboxfiles):
        with make_hsm_mailbox(mboxfiles, 1) as hsm_mb:
            hsm_mb._mailman._used_slots_array = [1] * sm.MESSAGES_PER_MAILBOX
            assert not hsm_mb._mailman._handleResponses(sm.EMPTYMAILBOX)


class TestCommunicate:

    def test_send_receive(self, mboxfiles):
        msg_processed = threading.Event()
        expired = False
        received_messages = []

        def spm_callback(msg_id, data):
            received_messages.append((msg_id, data))
            msg_processed.set()

        with make_hsm_mailbox(mboxfiles, 7) as hsm_mb:
            with make_spm_mailbox(mboxfiles) as spm_mm:
                spm_mm.registerMessageType(sm.EXTEND_CODE, spm_callback)
                REQUESTED_SIZE = 128 * MiB
                hsm_mb.sendExtendMsg(volume_data(), REQUESTED_SIZE)

                if not msg_processed.wait(MAILER_TIMEOUT):
                    expired = True

        assert not expired, 'message was not processed on time'
        assert received_messages == [(448, extend_message(REQUESTED_SIZE))]

    def test_send_reply(self, mboxfiles):
        HOST_ID = 3
        MSG_ID = HOST_ID * sm.SLOTS_PER_MAILBOX + 12
        SIZE = 2 * GiB
        with make_hsm_mailbox(mboxfiles, HOST_ID):
            with make_spm_mailbox(mboxfiles) as spm_mm:
                msg = sm.SPM_Extend_Message(volume_data(), SIZE)
                spm_mm.sendReply(MSG_ID, msg)

        inbox, outbox = read_mbox(mboxfiles)
        assert inbox == b'\0' * sm.MAILBOX_SIZE * MAX_HOSTS

        # proper MSG_ID is written, anything else is intact
        msg_offset = sm.MESSAGE_SIZE * MSG_ID
        assert outbox[:msg_offset] == b'\0' * msg_offset
        msg_end = msg_offset + sm.MESSAGE_SIZE
        assert outbox[msg_offset:msg_end] == extend_message(SIZE)
        assert outbox[msg_end:] == b'\0' * (
            sm.MAILBOX_SIZE * MAX_HOSTS - sm.MESSAGE_SIZE - msg_offset)

    def test_fill_slots(self, mboxfiles, monkeypatch):

        filled = threading.Event()
        orig_cmd = sm._mboxExecCmd

        def mbox_cmd_hook(*args, **kwargs):
            data = kwargs.get('data')
            if data and all(
                data[i:i + 1] != b"\0"
                for i in range(0, sm.MESSAGES_PER_MAILBOX, sm.MESSAGE_SIZE)
            ):
                filled.set()
            return orig_cmd(*args, **kwargs)

        monkeypatch.setattr(sm, "_mboxExecCmd", mbox_cmd_hook)

        with make_hsm_mailbox(mboxfiles, 1) as hsm_mb:
            for _ in range(sm.MESSAGES_PER_MAILBOX):
                hsm_mb.sendExtendMsg(volume_data(make_uuid()), 100)

            assert filled.wait(MAILER_TIMEOUT * 2)

    @pytest.mark.parametrize("delay", [0, 0.05])
    @pytest.mark.parametrize("messages", [
        1, 2, 4, 8, 16, 32, sm.MESSAGES_PER_MAILBOX])
    def test_roundtrip_events_enabled(self, mboxfiles, delay, messages):
        """
        Test roundtrip latency.

        This test is best run like this:

            $ tox -e storage tests/storage/mailbox_test.py -- \
                -k test_roundtrip \
                --log-cli-level=info \
                | grep stats

        Example output (trimmed):

            stats: messages=1 delay=0.000 best=0.215 average=0.215 worst=0.215
            stats: messages=1 delay=0.050 best=0.234 average=0.234 worst=0.234
            stats: messages=2 delay=0.000 best=0.231 average=0.233 worst=0.236
            stats: messages=2 delay=0.050 best=0.252 average=0.285 worst=0.319
            stats: messages=4 delay=0.000 best=0.257 average=0.258 worst=0.259
            stats: messages=4 delay=0.050 best=0.267 average=0.311 worst=0.354
            stats: messages=8 delay=0.000 best=0.231 average=0.344 worst=0.365
            stats: messages=8 delay=0.050 best=0.246 average=0.314 worst=0.391
            stats: messages=16 delay=0.000 best=0.244 average=0.379 worst=0.395
            stats: messages=16 delay=0.050 best=0.148 average=0.312 worst=0.393
            stats: messages=32 delay=0.000 best=0.260 average=0.378 worst=0.395
            stats: messages=32 delay=0.050 best=0.146 average=0.267 worst=0.402
            stats: messages=63 delay=0.000 best=0.269 average=0.429 worst=0.505
            stats: messages=63 delay=0.050 best=0.160 average=0.272 worst=0.423

        """
        times = self.roundtrip(mboxfiles, delay, messages)

        best = times[0]
        worst = times[-1]
        average = sum(times) / len(times)

        log.info(
            "stats: messages=%d delay=%.3f best=%.3f average=%.3f worst=%.3f",
            messages, delay, best, average, worst)

        # This is the slowest run when running locally:
        # stats: messages=63 delay=0.000 best=0.269 average=0.429 worst=0.505
        # In github CI this can be about twice slower. We use larger timeouts
        # to avoid flakeyness on slow CI.

        assert best < 5 * EVENT_INTERVAL
        assert average < 10 * EVENT_INTERVAL
        assert worst < 15 * EVENT_INTERVAL

    def test_roundtrip_events_disabled(self, mboxfiles, monkeypatch):
        config = make_config([("mailbox", "events_enable", "false")])
        monkeypatch.setattr(sm, "config", config)

        delay = 0.05
        messages = 8
        times = self.roundtrip(mboxfiles, delay, messages)

        best = times[0]
        worst = times[-1]
        average = sum(times) / len(times)

        log.info(
            "stats: messages=%d delay=%.3f best=%.3f average=%.3f worst=%.3f",
            messages, delay, best, average, worst)

        # Running locally takes:
        # stats: messages=8 delay=0.050 best=0.847 average=1.064 worst=1.243
        # Using larger timeout to avoid failures on slower environment.
        assert best < 5 * MONITOR_INTERVAL
        assert average < 6 * MONITOR_INTERVAL
        assert worst < 7 * MONITOR_INTERVAL

    def roundtrip(self, mboxfiles, delay, messages):
        with make_hsm_mailbox(mboxfiles, 7) as hsm_mb:
            with make_spm_mailbox(mboxfiles) as spm_mm:
                pool = FakePool(spm_mm)
                spm_callback = partial(
                    sm.SPM_Extend_Message.processRequest, pool)
                spm_mm.registerMessageType(sm.EXTEND_CODE, spm_callback)

                done = threading.Event()
                start = {}
                end = {}

                def reply_msg_callback(vol_data):
                    vol_id = vol_data['volumeID']
                    assert vol_id in start, "Missing request"
                    assert vol_id not in end, "Duplicate request"

                    end[vol_id] = time.monotonic()
                    log.info("got extension reply for volume %s, elapsed %s",
                             vol_id, end[vol_id] - start[vol_id])
                    if len(end) == messages:
                        log.info("done gathering all replies")
                        done.set()

                for _ in range(messages):
                    vol_id = make_uuid()
                    start[vol_id] = time.monotonic()
                    log.info("requesting to extend volume %s (delay=%.3f)",
                             vol_id, delay)
                    hsm_mb.sendExtendMsg(
                        volume_data(vol_id),
                        2 * GiB,
                        callbackFunction=reply_msg_callback)
                    time.sleep(delay)

                log.info("waiting for all replies")
                if not done.wait(MAILER_TIMEOUT):
                    raise RuntimeError("Roundtrip did not finish in time")

                log.info("waiting for messages clearing in SPM inbox")
                deadline = time.monotonic() + MAILER_TIMEOUT
                while True:
                    with io.open(mboxfiles.inbox, "rb") as f:
                        # Skip the event block, checking it is racy since hsm
                        # mail monitor writes events to this block.
                        f.seek(sm.MAILBOX_SIZE)
                        # check that SPM inbox was cleared
                        if f.read(sm.MAILBOX_SIZE) == sm.EMPTYMAILBOX:
                            break
                    if time.monotonic() >= deadline:
                        raise RuntimeError("Timeout clearing SPM inbox")

                    time.sleep(EVENT_INTERVAL)

        times = [end[k] - start[k] for k in start]
        times.sort()

        return times


class TestExtendMessage:

    def test_no_domain(self):
        vol_data = volume_data()
        del vol_data['domainID']
        with pytest.raises(sm.InvalidParameterException):
            sm.SPM_Extend_Message(vol_data, 0)

    def test_bad_size(self):
        with pytest.raises(sm.InvalidParameterException):
            sm.SPM_Extend_Message(volume_data(), -1)

    def test_process_request(self):
        MSG_ID = 7
        SIZE = GiB
        spm_mailer = FakeSPMMailer()
        pool = FakePool(spm_mailer)

        ret = sm.SPM_Extend_Message.processRequest(
            pool=pool, msgID=MSG_ID, payload=extend_message(SIZE))

        assert ret == {'status': {'code': 0, 'message': 'Done'}}
        vol_data = volume_data()
        assert pool.volume_data == {
            'volumeID': vol_data['volumeID'],
            'domainID': vol_data['domainID'],
            'size': SIZE
        }
        assert spm_mailer.msg_id == MSG_ID
        assert spm_mailer.msg.payload == extend_message(SIZE)
        assert spm_mailer.msg.callback is None


class TestValidation:

    def test_empty_mailbox(self):
        mailbox = sm.EMPTYMAILBOX
        assert not sm.SPM_MailMonitor.validateMailbox(mailbox, 7)

    def test_good_checksum(self):
        msg = b"x" * sm.MESSAGE_SIZE
        padding = sm.MAILBOX_SIZE - sm.MESSAGE_SIZE - sm.CHECKSUM_BYTES
        data = msg + padding * b"\0"
        n = sm.checksum(data)
        checksum = struct.pack('<l', n)
        mailbox = data + checksum
        assert sm.SPM_MailMonitor.validateMailbox(mailbox, 7)

    def test_bad_checksum(self):
        msg = b"x" * sm.MESSAGE_SIZE
        padding = sm.MAILBOX_SIZE - sm.MESSAGE_SIZE - sm.CHECKSUM_BYTES
        data = msg + padding * b"\0"
        mailbox = data + b"bad!"
        assert not sm.SPM_MailMonitor.validateMailbox(mailbox, 7)


class TestChecksum:

    @pytest.mark.parametrize("data,result,packed_result", [
        pytest.param(
            sm.EMPTYMAILBOX,
            0,
            b"\x00\x00\x00\x00",
            id="empty"),
        pytest.param(
            sm.CLEAN_MESSAGE * sm.MESSAGES_PER_MAILBOX + b"\0" * 62,
            4032,
            b"\xc0\x0f\x00\x00",
            id="clean notifications"),
        pytest.param(
            b"\xff" * 4092,
            1043460,
            b"\x04\xec\x0f\x00",
            id="maximum value"),
        pytest.param(
            bytes(bytearray(i % 256 for i in range(4092))),
            521226,
            b"\x0a\xf4\x07\x00",
            id="range pattern"),
        pytest.param(
            extend_message() + b"\0" * 4028,
            6455,
            b"\x37\x19\x00\x00",
            id="extend message pad tail"),
        pytest.param(
            b"\0" * 4028 + extend_message(),
            6455,
            b"\x37\x19\x00\x00",
            id="extend message pad head")
    ])
    def test_sanity(self, data, result, packed_result):
        assert sm.checksum(data) == result
        assert sm.packed_checksum(data) == packed_result


class TestWaitTimeout:

    @pytest.mark.parametrize("monitor_interval, expected_timeout", [
        (2, 3.0),     # production config
        (3, 4.5),     # production config
        (0.1, 0.15),  # testing config
        (0.2, 0.3),   # testing config
    ])
    def test_config(self, monitor_interval, expected_timeout):
        actual_timeout = sm.wait_timeout(monitor_interval)
        assert actual_timeout == pytest.approx(expected_timeout)


# Note: packed values were generated by historic version of
# mailbox.pack_uuid(), to ensure that we keep the packed format.
@pytest.mark.parametrize("value,packed", [
    pytest.param(
        "00000000-0000-4000-8000-000000000000",
        b"\x00\x00\x00\x00\x00\x00\x00\x80\x00@\x00\x00\x00\x00\x00\x00",
        id="smallest"),
    pytest.param(
        "ffffffff-ffff-4fff-bfff-ffffffffffff",
        b"\xff\xff\xff\xff\xff\xff\xff\xbf\xffO\xff\xff\xff\xff\xff\xff",
        id="highest"),
    pytest.param(
        "00010203-0405-4607-8001-020304050607",
        b"\x07\x06\x05\x04\x03\x02\x01\x80\x07F\x05\x04\x03\x02\x01\x00",
        id="some"),
])
def test_pack_uuid(value, packed):
    assert sm.pack_uuid(value) == packed
    assert sm.unpack_uuid(packed) == value


def test_pack_uuid_random():
    # Use pseudo random numbers for repeatable tests results.
    r = random.Random(42)
    for i in range(1000):
        u = str(uuid.UUID(int=r.randint(0, 2**128), version=4))
        packed = sm.pack_uuid(u)
        assert sm.unpack_uuid(packed) == u
