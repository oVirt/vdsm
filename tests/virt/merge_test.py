#
# Copyright 2020 Red Hat, Inc.
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

from vdsm.virt.vm import (
    BlockCopyActiveError,
    BlockJobUnrecoverableError,
    LiveMergeCleanupThread
)

from testlib import recorded


class FakeDrive:

    def __init__(self):
        self.volumeChain = []


class FakeDriveMonitor:

    def __init__(self):
        # driver_monitor is always enabled when calling cleanup.
        self.enabled = True

    def enable(self):
        self.enabled = True

    def disable(self):
        self.enabled = False


class FakeVM:

    def __init__(self):
        self.drive_monitor = FakeDriveMonitor()
        self.log = logging.getLogger()

    @recorded
    def _syncVolumeChain(self, drive):
        pass


class FakeLiveMergeCleanupThread(LiveMergeCleanupThread):
    """
    TODO: use VM/Storage methods instead of these so we can
    test for changes in the real code.
    """
    @recorded
    def tryPivot(self):
        pass

    @recorded
    def update_base_size(self):
        pass

    @recorded
    def teardown_top_volume(self):
        pass


def test_cleanup_initial():
    job = {
        "jobID": "fake-job-id",
        "topVolume": "fake-vol"
    }
    v = FakeVM()
    t = FakeLiveMergeCleanupThread(
        vm=v, job=job, drive=FakeDrive(), doPivot=True)

    assert t.state == LiveMergeCleanupThread.TRYING
    assert v.drive_monitor.enabled


def test_cleanup_done():
    job = {
        "jobID": "fake-job-id",
        "topVolume": "fake-vol"
    }
    v = FakeVM()
    drive = FakeDrive()
    t = FakeLiveMergeCleanupThread(vm=v, job=job, drive=drive, doPivot=True)
    t.start()
    t.join()

    assert t.state == LiveMergeCleanupThread.DONE
    assert v.drive_monitor.enabled
    assert v.__calls__ == [('_syncVolumeChain', (drive,), {})]
    assert t.__calls__ == [
        ('update_base_size', (), {}),
        ('tryPivot', (), {}),
        ('teardown_top_volume', (), {})
    ]


def test_cleanup_retry(monkeypatch):
    def recoverable_error(arg):
        raise BlockCopyActiveError("fake-job-id")

    monkeypatch.setattr(
        FakeLiveMergeCleanupThread, "tryPivot", recoverable_error)

    job = {
        "jobID": "fake-job-id",
        "topVolume": "fake-vol"
    }
    v = FakeVM()
    t = FakeLiveMergeCleanupThread(
        vm=v, job=job, drive=FakeDrive(), doPivot=True)
    t.start()
    t.join()

    assert t.state == LiveMergeCleanupThread.RETRY
    assert v.drive_monitor.enabled
    assert t.__calls__ == [('update_base_size', (), {})]


def test_cleanup_abort(monkeypatch):
    def unrecoverable_error(arg):
        raise BlockJobUnrecoverableError("fake-job-id", "error")

    monkeypatch.setattr(
        FakeLiveMergeCleanupThread, "tryPivot", unrecoverable_error)

    job = {
        "jobID": "fake-job-id",
        "topVolume": "fake-vol"
    }
    v = FakeVM()
    t = FakeLiveMergeCleanupThread(
        vm=v, job=job, drive=FakeDrive(), doPivot=True)
    t.start()
    t.join()

    assert t.state == LiveMergeCleanupThread.ABORT
    assert v.drive_monitor.enabled
    assert t.__calls__ == [('update_base_size', (), {})]
