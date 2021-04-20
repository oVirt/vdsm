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
import json
import libvirt
import logging
import os
import threading
import time
import yaml

import pytest

from vdsm.common import exception
from vdsm.common import response
from vdsm.common import xmlutils
from vdsm.common.units import GiB

from vdsm.virt import metadata
from vdsm.virt.domain_descriptor import DomainDescriptor
from vdsm.virt.livemerge import (
    CleanupThread,
    DriveMerger,
    Job,
    JobNotReadyError,
    JobPivotError,
)
from vdsm.virt.vm import Vm
from vdsm.virt.vmdevices import storage

from testlib import recorded, read_data, read_files

from . import vmfakelib as fake

TIMEOUT = 10

log = logging.getLogger("test")


class FakeTime(object):

    def __init__(self, value=0):
        self.time = value

    def __call__(self):
        return self.time


@pytest.fixture
def fake_time(monkeypatch):
    fake_time = FakeTime()
    monkeypatch.setattr(time, "monotonic", fake_time)
    return fake_time


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
    def sync_volume_chain(self, drive):
        pass


class FakeCleanupThread(CleanupThread):
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
    job = fake_job(pivot=True)
    v = FakeVM()
    t = FakeCleanupThread(vm=v, job=job, drive=FakeDrive())

    assert t.state == CleanupThread.TRYING
    assert v.drive_monitor.enabled


def test_cleanup_done():
    job = fake_job(pivot=True)
    v = FakeVM()
    drive = FakeDrive()
    t = FakeCleanupThread(vm=v, job=job, drive=drive)
    t.start()
    t.wait()

    assert t.state == CleanupThread.DONE
    assert v.drive_monitor.enabled
    assert v.__calls__ == [('sync_volume_chain', (drive,), {})]
    assert t.__calls__ == [
        ('update_base_size', (), {}),
        ('tryPivot', (), {}),
        ('teardown_top_volume', (), {})
    ]


@pytest.mark.parametrize("error", [
    JobNotReadyError("fake-job-id"),
    JobPivotError("fake-job-id", "fake libvirt error"),
    RuntimeError("unexpected error, bug?")
])
def test_cleanup_retry(monkeypatch, error):
    def tryPivot(arg):
        raise error

    monkeypatch.setattr(FakeCleanupThread, "tryPivot", tryPivot)

    job = fake_job(pivot=True)
    v = FakeVM()
    t = FakeCleanupThread(vm=v, job=job, drive=FakeDrive())
    t.start()
    t.wait()

    assert t.state == CleanupThread.FAILED
    assert v.drive_monitor.enabled
    assert t.__calls__ == [('update_base_size', (), {})]


class Config:
    """
    Load test configuration in tests/virt/{name}:

    00-before.xml
    01-commit.xml
    ...
    values.yml
    """

    def __init__(self, name):
        self.values = yaml.safe_load(
            read_data(os.path.join(name, "values.yml")))
        self.xmls = read_files(os.path.join(name, '*.xml'))


class RunningVM(Vm):

    def __init__(self, config):
        self._dom = FakeDomain(config)
        self.log = logging.getLogger()
        self.cif = fake.ClientIF()
        self._domain = DomainDescriptor(config.xmls["00-before.xml"])
        self.id = self._domain.id
        self._md_desc = metadata.Descriptor.from_xml(
            config.xmls["00-before.xml"])

        drive = config.values["drive"]
        self._devices = {
            "disk": [
                storage.Drive(
                    **drive,
                    volumeChain=xml_chain(config.xmls["00-before.xml"]),
                    log=self.log)
            ]
        }

        # Add the drives to to IRS:
        self.cif.irs.prepared_volumes = {
            (drive["domainID"], drive["imageID"], vol_id): vol_info
            for vol_id, vol_info in config.values["volumes"].items()
        }

        # Add the drive block info to fake domain.  This value is returned by
        # FakeDomain.blockInfo().
        top_volume = config.values["volumes"][drive["volumeID"]]
        self._dom.drives[drive["path"]] = {
            "capacity": top_volume["capacity"],
            "alloc": 0,
            "physical": top_volume["apparentsize"],
        }

        self.conf = self._conf_devices(config)
        self.conf["vmId"] = config.values["vm-id"]
        self.conf["xml"] = config.xmls["00-before.xml"]

        self._external = False  # Used when syncing metadata.
        self.drive_monitor = FakeDriveMonitor()
        self._confLock = threading.Lock()
        self._drive_merger = DriveMerger(self)

    def _conf_devices(self, config):
        drive = dict(config.values["drive"])
        drive["volumeChain"] = xml_chain(config.xmls["00-before.xml"])
        return {"devices": [drive]}

    def cont(self):
        return response.success()


class FakeDomain:

    def __init__(self, config):
        self.log = logging.getLogger()
        self._id = config.values["vm-id"]
        self._config = config

        # Variables which are not part of virtDomain interface, mananged by the
        # tests.
        self.xml = config.xmls["00-before.xml"]
        self.metadata = "<vm><jobs>{}</jobs></vm>"
        self.aborted = threading.Event()
        self.block_jobs = {}
        # Keeps block info dict for every drive, returned by blockInfo().
        self.drives = {}

    def UUIDString(self):
        return self._id

    def setMetadata(self, type, xml, prefix, uri, flags=0):
        # libvirt's setMetadata will add namespace uri to metadata tags in
        # the domain xml, here we care for volume chain sync after a
        # successful pivot.
        self.metadata = xml

    def XMLDesc(self, flags=0):
        return self.xml

    def all_channels(self):
        return []

    def blockCommit(self, drive, base_target, top_target, bandwidth, flags=0):
        if flags & libvirt.VIR_DOMAIN_BLOCK_COMMIT_ACTIVE:
            job_type = libvirt.VIR_DOMAIN_BLOCK_JOB_TYPE_ACTIVE_COMMIT
        else:
            job_type = libvirt.VIR_DOMAIN_BLOCK_JOB_TYPE_COMMIT

        self.block_jobs[drive] = {
            'bandwidth': 0,
            'cur': 0,
            'end': 1024**3,
            'type': job_type
        }

        # The test should simulate commit-ready once the active commit
        # has done mirroring the volume.
        self.xml = self._config.xmls["01-commit.xml"]

    def blockJobInfo(self, drive, flags=0):
        return self.block_jobs.get(drive, {})

    def blockJobAbort(self, drive, flags=0):
        if flags & libvirt.VIR_DOMAIN_BLOCK_JOB_ABORT_PIVOT:
            # The test should simulate abort-ready such that the cleanup
            # thread would stop waiting for libvirt's domain xml updated
            # volumes chain after pivot is done.
            self.xml = self._config.xmls["03-abort.xml"]
        else:
            # Aborting without pivot attempt will revert to original dom xml.
            self.xml = self._config.xmls["00-before.xml"]

        self.aborted.set()
        del self.block_jobs[drive]

    def blockInfo(self, path, flags=0):
        """
        Return drive block info tuple (capacity, alloc, physical).

        Libvirt supports drive path ("/path/to/top/volume"), drive name (e.g.
        "sda"), or name[index] notation ("sda[4]").  We support only drive
        path.

        For more info see:
        https://libvirt.org/html/libvirt-libvirt-domain.html#virDomainGetBlockInfo
        """
        if path not in self.drives:
            raise fake.libvirt_error(
                [libvirt.VIR_ERR_INTERNAL_ERROR], "Drive not found")
        drive = self.drives[path]
        return drive["capacity"], drive["alloc"], drive["physical"]


def test_merger_dump_jobs(fake_time):
    config = Config('active-merge')
    sd_id = config.values["drive"]["domainID"]
    img_id = config.values["drive"]["imageID"]
    merge_params = config.values["merge_params"]
    job_id = merge_params["jobUUID"]
    top_id = merge_params["topVolUUID"]
    base_id = merge_params["baseVolUUID"]

    vm = RunningVM(config)

    top = vm.cif.irs.prepared_volumes[(sd_id, img_id, top_id)]
    base = vm.cif.irs.prepared_volumes[(sd_id, img_id, base_id)]

    # No jobs yet.

    assert vm._drive_merger.dump_jobs() == {}

    vm.merge(**merge_params)

    # Merge was started, new jobs should be in the dump.

    assert vm._drive_merger.dump_jobs() == {
        job_id : {
            "bandwidth": merge_params["bandwidth"],
            "base": merge_params["baseVolUUID"],
            "disk": merge_params["driveSpec"],
            "drive": "sda",
            "state": Job.EXTEND,
            "extend": {
                "attempt": 1,
                "base_size": base["apparentsize"],
                "top_size": top["apparentsize"],
                "started": fake_time.time,
            },
            "pivot": None,
            "id": job_id,
            "top": merge_params["topVolUUID"],
        }
    }


def test_merger_load_jobs(fake_time):
    config = Config('active-merge')
    sd_id = config.values["drive"]["domainID"]
    img_id = config.values["drive"]["imageID"]
    merge_params = config.values["merge_params"]
    job_id = merge_params["jobUUID"]
    top_id = merge_params["topVolUUID"]
    base_id = merge_params["baseVolUUID"]

    vm = RunningVM(config)

    top = vm.cif.irs.prepared_volumes[(sd_id, img_id, top_id)]
    base = vm.cif.irs.prepared_volumes[(sd_id, img_id, base_id)]

    assert vm._drive_merger.dump_jobs() == {}

    # Load jobs, simulating recovery flow.

    dumped_jobs = {
        job_id : {
            "bandwidth": merge_params["bandwidth"],
            "base": merge_params["baseVolUUID"],
            "disk": merge_params["driveSpec"],
            "drive": "sda",
            "state": Job.EXTEND,
            "extend": {
                "attempt": 1,
                "base_size": base["apparentsize"],
                "top_size": top["apparentsize"],
                "started": fake_time.time,
            },
            "pivot": None,
            "id": job_id,
            "top": merge_params["topVolUUID"],
        }
    }

    vm._drive_merger.load_jobs(dumped_jobs)
    assert vm._drive_merger.dump_jobs() == dumped_jobs


def test_active_merge(monkeypatch):
    monkeypatch.setattr(CleanupThread, "WAIT_INTERVAL", 0.01)

    config = Config('active-merge')
    sd_id = config.values["drive"]["domainID"]
    img_id = config.values["drive"]["imageID"]
    merge_params = config.values["merge_params"]
    job_id = merge_params["jobUUID"]
    top_id = merge_params["topVolUUID"]
    base_id = merge_params["baseVolUUID"]

    vm = RunningVM(config)

    # No active block jobs before calling merge.
    assert vm.query_jobs() == {}

    vm.merge(**merge_params)

    # Merge persists the job with EXTEND state.
    persisted_job = parse_jobs(vm)[job_id]
    assert persisted_job["state"] == Job.EXTEND

    # Libvit block job was not started yet.
    assert "sda" not in vm._dom.block_jobs

    # Because the libvirt job was not started, report default live info.
    assert vm.query_jobs() == {
        job_id : {
            "bandwidth" : 0,
            "blockJobType": "commit",
            "cur": "0",
            "drive": "sda",
            "end": "0",
            "id": job_id,
            "imgUUID": img_id,
            "jobType": "block"
        }
    }

    # query_jobs() keeps job in EXTEND state.
    persisted_job = parse_jobs(vm)[job_id]
    assert persisted_job["state"] == Job.EXTEND

    # We should extend to next volume size based on base and top currrent size,
    # base volume capacity, and chunk size configuration.
    top = vm.cif.irs.prepared_volumes[(sd_id, img_id, top_id)]
    base = vm.cif.irs.prepared_volumes[(sd_id, img_id, base_id)]
    max_alloc = base["apparentsize"] + top["apparentsize"]
    drive = vm.getDiskDevices()[0]
    new_size = drive.getNextVolumeSize(max_alloc, top["capacity"])

    simulate_volume_extension(vm, base_id)

    assert base["apparentsize"] == new_size

    # Extend callback started a commit and persisted the job.
    persisted_job = parse_jobs(vm)[job_id]
    assert persisted_job["state"] == Job.COMMIT
    assert persisted_job["extend"] is None

    # And start a libvirt active block commit block job.
    block_job = vm._dom.block_jobs["sda"]
    assert block_job["type"] == libvirt.VIR_DOMAIN_BLOCK_JOB_TYPE_ACTIVE_COMMIT

    assert vm.query_jobs() == {
        job_id : {
            "bandwidth" : 0,
            "blockJobType": "commit",
            "cur": str(block_job["cur"]),
            "drive": "sda",
            "end": str(block_job["end"]),
            "id": job_id,
            "imgUUID": img_id,
            "jobType": "block"
        }
    }

    # query_jobs() keeps job in COMMIT state.
    persisted_job = parse_jobs(vm)[job_id]
    assert persisted_job["state"] == Job.COMMIT

    # Check block job status while in progress.
    block_job["cur"] = block_job["end"] // 2

    assert vm.query_jobs() == {
        job_id : {
            "bandwidth" : 0,
            "blockJobType": "commit",
            "cur": str(block_job["cur"]),
            "drive": "sda",
            "end": str(block_job["end"]),
            "id": job_id,
            "imgUUID": img_id,
            "jobType": "block"
        }
    }

    # Check job status when job finished, but before libvirt
    # updated the xml.
    block_job["cur"] = block_job["end"]

    assert vm.query_jobs() == {
        job_id : {
            "bandwidth" : 0,
            "blockJobType": "commit",
            "cur": str(block_job["cur"]),
            "drive": "sda",
            "end": str(block_job["end"]),
            "id": job_id,
            "imgUUID": img_id,
            "jobType": "block"
        }
    }

    # Simulate completion of backup job - libvirt updates the xml.
    vm._dom.xml = config.xmls["02-commit-ready.xml"]

    # Trigger cleanup and pivot attempt.
    vm.query_jobs()

    # query_jobs() switched job to CLEANUP state.
    persisted_job = parse_jobs(vm)[job_id]
    assert persisted_job["state"] == Job.CLEANUP
    assert persisted_job["pivot"]

    # Wait for cleanup to abort the block job as part of the pivot attempt.
    aborted = vm._dom.aborted.wait(TIMEOUT)
    assert aborted, "Timeout waiting for blockJobAbort() call"

    # Since the job switched to CLEANUP state, we don't query libvirt live info
    # again, and the job reports the last live info.
    assert vm.query_jobs() == {
        job_id : {
            "bandwidth" : 0,
            "blockJobType": "commit",
            "cur": str(block_job["cur"]),
            "drive": "sda",
            "end": str(block_job["end"]),
            "id": job_id,
            "imgUUID": img_id,
            "jobType": "block"
        }
    }

    # Set the abort-ready state after cleanup has called active commit abort.
    vm._dom.xml = config.xmls["04-abort-ready.xml"]

    # Check for cleanup completion.
    wait_for_cleanup(vm)

    # When cleanup finished, job was untracked and jobs were persisted.
    persisted_jobs = parse_jobs(vm)
    assert persisted_jobs == {}

    # The fake domain mocks the setMetadata method and store the input as is,
    # domain xml is not manipulated by the test as xml due to namespacing
    # issues, so we only compare the resulting volume chain both between
    # updated metadata and the expected xml.
    expected_volumes_chain = xml_chain(config.xmls["05-after.xml"])
    assert metadata_chain(vm._dom.metadata) == expected_volumes_chain

    # Top volume gets torn down.
    assert (sd_id, img_id, top_id) not in vm.cif.irs.prepared_volumes

    # Drive volume chain is updated and monitoring is back to enabled.
    drive = vm.getDiskDevices()[0]
    assert drive.volumeChain == expected_volumes_chain
    assert vm.drive_monitor.enabled


def test_internal_merge():
    config = Config('internal-merge')
    sd_id = config.values["drive"]["domainID"]
    img_id = config.values["drive"]["imageID"]
    merge_params = config.values["merge_params"]
    job_id = merge_params["jobUUID"]
    top_id = merge_params["topVolUUID"]
    base_id = merge_params["baseVolUUID"]

    vm = RunningVM(config)

    assert vm.query_jobs() == {}

    vm.merge(**merge_params)

    # Merge persists job in EXTEND state.
    persisted_job = parse_jobs(vm)[job_id]
    assert persisted_job["state"] == Job.EXTEND

    simulate_volume_extension(vm, base_id)

    # Extend triggers a commit, starting a libvirt block commit block job.
    block_job = vm._dom.block_jobs["sda"]
    assert block_job["type"] == libvirt.VIR_DOMAIN_BLOCK_JOB_TYPE_COMMIT

    # And persisting job in COMMIT state.
    persisted_job = parse_jobs(vm)[job_id]
    assert persisted_job["state"] == Job.COMMIT
    assert not persisted_job["pivot"]

    # Active jobs after calling merge.
    assert vm.query_jobs() == {
        job_id : {
            "bandwidth" : 0,
            "blockJobType": "commit",
            "cur": str(block_job["cur"]),
            "drive": "sda",
            "end": str(block_job["end"]),
            "id": job_id,
            "imgUUID": img_id,
            "jobType": "block"
        }
    }

    # Check block job status while in progress.
    block_job["cur"] = block_job["end"] // 2

    assert vm.query_jobs() == {
        job_id : {
            "bandwidth" : 0,
            "blockJobType": "commit",
            "cur": str(block_job["cur"]),
            "drive": "sda",
            "end": str(block_job["end"]),
            "id": job_id,
            "imgUUID": img_id,
            "jobType": "block"
        }
    }

    # Check job status when job finished, but before libvirt
    # updated the xml.
    block_job["cur"] = block_job["end"]

    assert vm.query_jobs() == {
        job_id : {
            "bandwidth" : 0,
            "blockJobType": "commit",
            "cur": str(block_job["cur"]),
            "drive": "sda",
            "end": str(block_job["end"]),
            "id": job_id,
            "imgUUID": img_id,
            "jobType": "block"
        }
    }

    # Simulate job completion:
    # 1. libvirt removes the job.
    # 2. libvirt changes the xml.
    del vm._dom.block_jobs["sda"]
    vm._dom.xml = config.xmls["02-after.xml"]

    # Querying the job when the job has gone should switch the job state to
    # CLEANUP and start a cleanup thread.
    info = vm.query_jobs()

    # Query reports the default status entry before cleanup is done.
    assert info == {
        job_id : {
            "bandwidth" : 0,
            "blockJobType": "commit",
            "cur": "0",
            "drive": "sda",
            "end": "0",
            "id": job_id,
            "imgUUID": img_id,
            "jobType": "block"
        }
    }

    # Job persisted now in CLEANUP state.
    persisted_job = parse_jobs(vm)[job_id]
    assert persisted_job["state"] == Job.CLEANUP
    assert not persisted_job["pivot"]

    # Check for cleanup completion.
    wait_for_cleanup(vm)

    # Job removed from persisted jobs.
    assert parse_jobs(vm) == {}

    # Volumes chain is updated in domain metadata without top volume.
    expected_volumes_chain = xml_chain(config.xmls["02-after.xml"])
    assert metadata_chain(vm._dom.metadata) == expected_volumes_chain

    # Top snapshot is merged into removed snapshot and its volume is torn down.
    assert (sd_id, img_id, top_id) not in vm.cif.irs.prepared_volumes

    drive = vm.getDiskDevices()[0]
    assert drive.volumeChain == expected_volumes_chain
    assert vm.drive_monitor.enabled


def test_extend_timeout_recover(fake_time):
    config = Config('active-merge')
    sd_id = config.values["drive"]["domainID"]
    img_id = config.values["drive"]["imageID"]
    merge_params = config.values["merge_params"]
    job_id = merge_params["jobUUID"]
    base_id = merge_params["baseVolUUID"]
    top_id = merge_params["topVolUUID"]

    vm = RunningVM(config)

    vm.merge(**merge_params)

    # Find base and top sizes. They do not change during this test.
    base = vm.cif.irs.prepared_volumes[(sd_id, img_id, base_id)]
    top = vm.cif.irs.prepared_volumes[(sd_id, img_id, top_id)]
    base_size = base["apparentsize"]
    top_size = top["apparentsize"]

    # Job starts at EXTEND state, tracking the first extend attempt.
    persisted_job = parse_jobs(vm)[job_id]
    assert persisted_job["state"] == Job.EXTEND
    assert persisted_job["extend"] == {
        "attempt": 1,
        "started": fake_time.time,
        "base_size": base_size,
        "top_size": top_size,
    }

    # First extend request was sent.
    assert len(vm.cif.irs.extend_requests) == 1

    # Simulate extend timeout, triggering the next successful extend attempt.
    fake_time.time += DriveMerger.EXTEND_TIMEOUT + 1
    vm.query_jobs()

    # Job tracks the second extend attempt.
    persisted_job = parse_jobs(vm)[job_id]
    assert persisted_job["state"] == Job.EXTEND
    assert persisted_job["extend"] == {
        "attempt": 2,
        "started": fake_time.time,
        "base_size": base_size,
        "top_size": top_size,
    }

    # Second extend request was sent.
    assert len(vm.cif.irs.extend_requests) == 2

    simulate_volume_extension(vm, merge_params["baseVolUUID"])

    # Extend completed, moving job to COMMIT state.
    persisted_job = parse_jobs(vm)[job_id]
    assert persisted_job["state"] == Job.COMMIT


def test_extend_use_original_base_size(fake_time):
    config = Config('active-merge')
    sd_id = config.values["drive"]["domainID"]
    img_id = config.values["drive"]["imageID"]
    merge_params = config.values["merge_params"]
    job_id = merge_params["jobUUID"]
    base_id = merge_params["baseVolUUID"]

    vm = RunningVM(config)

    vm.merge(**merge_params)

    # Find base volume size.
    base = vm.cif.irs.prepared_volumes[(sd_id, img_id, base_id)]
    base_size = base["apparentsize"]

    # Job starts at EXTEND state, tracking the first extend attempt.
    persisted_job = parse_jobs(vm)[job_id]
    assert persisted_job["state"] == Job.EXTEND
    assert persisted_job["extend"]["attempt"] == 1
    assert persisted_job["extend"]["base_size"] == base_size

    # Just when the extend timed out, the base volume was extended, but the
    # extend callback was not called yet.
    new_size1 = vm.cif.irs.extend_requests[0][2]
    base["apparentsize"] = new_size1

    # Simulate extend timeout, triggering the next extend attempt.
    fake_time.time += DriveMerger.EXTEND_TIMEOUT + 1
    vm.query_jobs()

    # Job tracks the second extend attempt, using the original base size.
    persisted_job = parse_jobs(vm)[job_id]
    assert persisted_job["state"] == Job.EXTEND
    assert persisted_job["extend"]["attempt"] == 2
    assert persisted_job["extend"]["base_size"] == base_size

    # Second extend request was sent same size. This extend does not have any
    # effect since the volume was already extended.
    new_size2 = vm.cif.irs.extend_requests[1][2]
    assert new_size2 == new_size1

    # The first extend callback is called now, moving the job to COMMIT state.
    simulate_volume_extension(vm, base_id)
    persisted_job = parse_jobs(vm)[job_id]
    assert persisted_job["state"] == Job.COMMIT


def test_extend_use_current_top_size(fake_time):
    config = Config('active-merge')
    sd_id = config.values["drive"]["domainID"]
    img_id = config.values["drive"]["imageID"]
    merge_params = config.values["merge_params"]
    job_id = merge_params["jobUUID"]
    top_id = merge_params["topVolUUID"]

    vm = RunningVM(config)

    vm.merge(**merge_params)

    top = vm.cif.irs.prepared_volumes[(sd_id, img_id, top_id)]

    # Job starts at EXTEND state, tracking the first extend attempt.
    persisted_job = parse_jobs(vm)[job_id]
    assert persisted_job["state"] == Job.EXTEND
    assert persisted_job["extend"]["attempt"] == 1

    # First extend request was sent based on base and top size.
    new_size1 = vm.cif.irs.extend_requests[0][2]

    # While waiting for extend completion, top volume was extended.
    drive = vm.getDiskDevices()[0]
    top["apparentsize"] += GiB
    vm._dom.drives[drive.path]["physical"] = top["apparentsize"]

    # Simulate extend timeout, triggering the next extend attempt.
    fake_time.time += DriveMerger.EXTEND_TIMEOUT + 1
    vm.query_jobs()

    # Job tracks the second extend attempt.
    persisted_job = parse_jobs(vm)[job_id]
    assert persisted_job["state"] == Job.EXTEND
    assert persisted_job["extend"]["attempt"] == 2

    # Second extend request was sent with bigger volume size.
    new_size2 = vm.cif.irs.extend_requests[1][2]
    assert new_size2 == new_size1 + GiB


def test_extend_timeout_all(fake_time):
    config = Config('active-merge')
    merge_params = config.values["merge_params"]
    job_id = merge_params["jobUUID"]

    vm = RunningVM(config)

    vm.merge(**merge_params)

    # Job starts with EXTEND state, performing the first extend attempt.
    persisted_job = parse_jobs(vm)[job_id]
    assert persisted_job["state"] == Job.EXTEND
    assert persisted_job["extend"]["attempt"] == 1
    assert persisted_job["extend"]["started"] == fake_time.time
    assert len(vm.cif.irs.extend_requests) == 1

    for attempt in range(2, DriveMerger.EXTEND_ATTEMPTS + 1):
        # Simulate an extend timeout.
        fake_time.time += DriveMerger.EXTEND_TIMEOUT + 1

        # Querying jobs detects an extend timeout...
        vm.query_jobs()

        # Update extend info in the metadata and send new extend request.
        persisted_job = parse_jobs(vm)[job_id]
        assert persisted_job["state"] == Job.EXTEND
        assert persisted_job["extend"]["attempt"] == attempt
        assert persisted_job["extend"]["started"] == fake_time.time
        assert len(vm.cif.irs.extend_requests) == attempt

    # Simulate the last extend timeout.
    fake_time.time += DriveMerger.EXTEND_TIMEOUT + 1

    # The next query will abort the job.
    assert vm.query_jobs() == {}

    # Job removed from persisted jobs.
    assert parse_jobs(vm) == {}

    # Simulate slow extend completing after jobs was untracked.
    simulate_volume_extension(vm, merge_params["baseVolUUID"])


def test_extend_error_recover(fake_time):
    config = Config('active-merge')
    sd_id = config.values["drive"]["domainID"]
    img_id = config.values["drive"]["imageID"]
    merge_params = config.values["merge_params"]
    job_id = merge_params["jobUUID"]
    base_id = merge_params["baseVolUUID"]
    top_id = merge_params["topVolUUID"]

    vm = RunningVM(config)

    vm.merge(**merge_params)

    # Find base and top size. They do not change during this test.
    top = vm.cif.irs.prepared_volumes[(sd_id, img_id, top_id)]
    base = vm.cif.irs.prepared_volumes[(sd_id, img_id, base_id)]
    base_size = base["apparentsize"]
    top_size = top["apparentsize"]

    # Job starts at EXTEND state, tracking the first extend attempt.
    persisted_job = parse_jobs(vm)[job_id]
    assert persisted_job["state"] == Job.EXTEND
    assert persisted_job["extend"] == {
        "attempt": 1,
        "started": fake_time.time,
        "base_size": base_size,
        "top_size": top_size,
    }

    # First extend request was sent.
    assert len(vm.cif.irs.extend_requests) == 1

    # Advance the time so we can check that extend track the start time of the
    # second attempt.
    fake_time.time += 1

    # Simulate extend error triggering the next successful extend attempt.
    with pytest.raises(RuntimeError):
        simulate_extend_error(vm)

    # Job tracks the second extend attempt.
    persisted_job = parse_jobs(vm)[job_id]
    assert persisted_job["state"] == Job.EXTEND
    assert persisted_job["extend"] == {
        "attempt": 2,
        "started": fake_time.time,
        "base_size": base_size,
        "top_size": top_size,
    }

    # Second extend request was sent.
    assert len(vm.cif.irs.extend_requests) == 1

    simulate_volume_extension(vm, merge_params["baseVolUUID"])

    # Extend completed, moving job to COMMIT state.
    persisted_job = parse_jobs(vm)[job_id]
    assert persisted_job["state"] == Job.COMMIT


def test_extend_error_all(fake_time):
    config = Config('active-merge')
    merge_params = config.values["merge_params"]
    job_id = merge_params["jobUUID"]

    vm = RunningVM(config)

    vm.merge(**merge_params)

    # Job starts with EXTEND state, performing the first extend attempt.
    persisted_job = parse_jobs(vm)[job_id]
    assert persisted_job["state"] == Job.EXTEND
    assert persisted_job["extend"]["attempt"] == 1
    assert persisted_job["extend"]["started"] == fake_time.time
    assert len(vm.cif.irs.extend_requests) == 1

    for attempt in range(2, DriveMerger.EXTEND_ATTEMPTS + 1):
        # Advance the clock to test that we reset extend start time.
        fake_time.time += 1

        # Simulate extend error, triggring a retry...
        with pytest.raises(RuntimeError):
            simulate_extend_error(vm)

        # The callabck updates extend metadata and sends new extend request.
        persisted_job = parse_jobs(vm)[job_id]
        assert persisted_job["state"] == Job.EXTEND
        assert persisted_job["extend"]["attempt"] == attempt
        assert persisted_job["extend"]["started"] == fake_time.time
        assert len(vm.cif.irs.extend_requests) == 1

    # The last error wil untrack the job.
    with pytest.raises(RuntimeError):
        simulate_extend_error(vm)

    # Job was untracked.
    assert vm.query_jobs() == {}
    assert parse_jobs(vm) == {}


def test_extend_skipped():
    config = Config('active-merge')
    sd_id = config.values["drive"]["domainID"]
    img_id = config.values["drive"]["imageID"]
    merge_params = config.values["merge_params"]
    job_id = merge_params["jobUUID"]
    base_id = merge_params["baseVolUUID"]

    vm = RunningVM(config)

    # Simulate base volume extended to the maximum size.
    drive = vm.getDiskDevices()[0]
    base = vm.cif.irs.prepared_volumes[(sd_id, img_id, base_id)]
    max_size = drive.getMaxVolumeSize(base["capacity"])
    base['apparentsize'] = max_size
    vm._dom.drives[drive.path]["physical"] = max_size

    vm.merge(**merge_params)

    # Since base cannot be extended, jobs skips EXTEND phase.
    persisted_job = parse_jobs(vm)[job_id]
    assert persisted_job["state"] == Job.COMMIT


def test_active_merge_canceled_during_commit():
    config = Config('active-merge')
    merge_params = config.values["merge_params"]
    job_id = merge_params["jobUUID"]
    base_id = merge_params["baseVolUUID"]

    vm = RunningVM(config)

    assert vm.query_jobs() == {}

    vm.merge(**merge_params)

    simulate_volume_extension(vm, base_id)

    # Job switched to COMMIT state.
    persisted_job = parse_jobs(vm)[job_id]
    assert persisted_job["state"] == Job.COMMIT

    assert vm.query_jobs() == {
        job_id : {
            "bandwidth" : 0,
            "blockJobType": "commit",
            "cur": "0",
            "drive": "sda",
            "end": "1073741824",
            "id": job_id,
            "imgUUID": merge_params["driveSpec"]["imageID"],
            "jobType": "block"
        }
    }

    # Cancel the block job. This simulates a scenario where a user
    # aborts running block job from virsh.
    vm._dom.blockJobAbort("sda")

    vm.query_jobs()

    # Job switched to CLEANUP state.
    persisted_job = parse_jobs(vm)[job_id]
    assert persisted_job["state"] == Job.CLEANUP
    assert not persisted_job["pivot"]

    # Cleanup is done running.
    wait_for_cleanup(vm)

    # Job removed from persisted jobs.
    assert parse_jobs(vm) == {}

    # Volume chains state should be as it was before merge.
    assert vm._dom.xml == config.xmls["00-before.xml"]
    expected_volumes_chain = xml_chain(config.xmls["00-before.xml"])
    assert metadata_chain(vm._dom.metadata) == expected_volumes_chain

    # Drive chain is unchanged and monitoring is enabled.
    drive = vm.getDiskDevices()[0]
    assert drive.volumeID == config.values["drive"]["volumeID"]
    assert drive.volumeChain == expected_volumes_chain
    assert vm.drive_monitor.enabled


def test_active_merge_canceled_during_cleanup(monkeypatch):
    config = Config('active-merge')
    merge_params = config.values["merge_params"]
    job_id = merge_params["jobUUID"]
    base_id = merge_params["baseVolUUID"]

    vm = RunningVM(config)

    assert vm.query_jobs() == {}

    vm.merge(**merge_params)

    simulate_volume_extension(vm, base_id)

    # Job switched to COMMIT state.
    persisted_job = parse_jobs(vm)[job_id]
    assert persisted_job["state"] == Job.COMMIT

    # Make pivot fail during cleanup.
    with monkeypatch.context() as c:

        def fail(drive, flags=0):
            raise fake.libvirt_error(
                [libvirt.VIR_ERR_INTERNAL_ERROR], "fake libvirt error")

        c.setattr(vm._dom, "blockJobAbort", fail)

        # Simulate job becoming ready.
        block_job = vm._dom.block_jobs["sda"]
        block_job["cur"] = block_job["end"]
        vm._dom.xml = config.xmls["02-commit-ready.xml"]

        # Trigger the first cleanup.
        vm.query_jobs()

        persisted_job = parse_jobs(vm)[job_id]
        assert persisted_job["state"] == Job.CLEANUP
        assert persisted_job["pivot"]

        # Wait until the first cleanup completes.
        if not vm._drive_merger.wait_for_cleanup(TIMEOUT):
            raise RuntimeError("Timeout waiting for cleanup")

    # Cancel the block job. This simulates a scenario where a user aborts
    # running block job from virsh.
    vm._dom.blockJobAbort("sda")

    # Trigger the next cleanup.
    vm.query_jobs()

    # Since job has gone, the job should complete without trying to pivot.
    persisted_job = parse_jobs(vm)[job_id]
    assert persisted_job["state"] == Job.CLEANUP
    assert not persisted_job["pivot"]

    wait_for_cleanup(vm)

    # Job removed from persisted jobs.
    assert parse_jobs(vm) == {}


def test_block_job_info_error(monkeypatch):
    config = Config("internal-merge")
    merge_params = config.values["merge_params"]
    job_id = merge_params["jobUUID"]
    base_id = merge_params["baseVolUUID"]

    vm = RunningVM(config)

    vm.merge(**merge_params)

    simulate_volume_extension(vm, base_id)

    # Job switched to COMMIT state.
    persisted_job = parse_jobs(vm)[job_id]
    assert persisted_job["state"] == Job.COMMIT

    with monkeypatch.context() as mc:

        # Simulate failing blockJobInfo call.
        def blockJobInfo(*args, **kwargs):
            raise fake.libvirt_error(
                [libvirt.VIR_ERR_INTERNAL_ERROR], "Block job info failed")

        mc.setattr(FakeDomain, "blockJobInfo", blockJobInfo)

        # We cannot get live job info, so we return default values.
        assert vm.query_jobs() == {
            job_id: {
                "bandwidth" : 0,
                "blockJobType": "commit",
                "cur": "0",
                "drive": "sda",
                "end": "0",
                "id": job_id,
                "imgUUID": merge_params["driveSpec"]["imageID"],
                "jobType": "block"
            }
        }

    # Libvirt call succeeds so we return live info from libvit.
    assert vm.query_jobs() == {
        job_id: {
            "bandwidth" : 0,
            "blockJobType": "commit",
            "cur": "0",
            "drive": "sda",
            "end": "1073741824",
            "id": job_id,
            "imgUUID": merge_params["driveSpec"]["imageID"],
            "jobType": "block"
        }
    }


def test_merge_commit_error(monkeypatch):
    config = Config("internal-merge")
    merge_params = config.values["merge_params"]
    base_id = merge_params["baseVolUUID"]

    vm = RunningVM(config)

    def commit_error(*args, **kwargs):
        raise fake.libvirt_error(
            [libvirt.VIR_ERR_INTERNAL_ERROR], "Block commit failed")

    monkeypatch.setattr(FakeDomain, "blockCommit", commit_error)

    vm.merge(**merge_params)

    # Extend completion trigger failed commit.
    with pytest.raises(exception.MergeFailed):
        simulate_volume_extension(vm, base_id)

    # Job was untracked.
    assert vm.query_jobs() == {}

    # Job removed from persisted jobs.
    assert parse_jobs(vm) == {}


def test_merge_job_already_exists(monkeypatch):
    config = Config("internal-merge")
    merge_params = config.values["merge_params"]
    job_id = merge_params["jobUUID"]

    vm = RunningVM(config)

    # Calling merge twice will fail the second call with same block
    # job already tracked from first call.
    vm.merge(**merge_params)
    assert len(vm.query_jobs()) == 1

    with pytest.raises(exception.MergeFailed):
        vm.merge(**merge_params)

    # Existing job is kept.
    assert len(vm.query_jobs()) == 1
    assert parse_jobs(vm)[job_id]


def test_merge_base_too_small(monkeypatch):
    config = Config("internal-merge")
    merge_params = config.values["merge_params"]

    vm = RunningVM(config)

    # Ensure that base volume is raw and smaller than top,
    # engine is responsible for extending the raw base volume
    # before merge is called.
    base_vol = config.values["volumes"][merge_params["baseVolUUID"]]
    top_vol = config.values["volumes"][merge_params["topVolUUID"]]
    base_vol["capacity"] = top_vol["capacity"] // 2
    base_vol["format"] = "RAW"

    with pytest.raises(exception.DestinationVolumeTooSmall):
        vm.merge(**merge_params)

    assert vm.query_jobs() == {}
    assert parse_jobs(vm) == {}


def simulate_volume_extension(vm, vol_id):
    _, vol_info, new_size, callback = vm.cif.irs.extend_requests.pop(0)

    drive = vm.getDiskDevices()[0]
    base = vm.cif.irs.prepared_volumes[(drive.domainID, drive.imageID, vol_id)]

    # We have to update both prepared volume info and libvirt info.
    base['apparentsize'] = new_size
    vm._dom.drives[drive.path]["physical"] = new_size

    callback(vol_info)


def simulate_extend_error(vm):
    _, vol_info, _, callback = vm.cif.irs.extend_requests.pop(0)
    # Don't update the volume size to fail the size verification.
    callback(vol_info)


def wait_for_cleanup(vm):
    log.info("Waiting for cleanup")

    deadline = time.monotonic() + TIMEOUT
    # Block job monitor excutes updateVmJobs method periodically to update
    # on the status of managed block jobs.
    vm.updateVmJobs()
    while vm.hasVmJobs:
        time.sleep(0.01)
        if time.monotonic() > deadline:
            raise RuntimeError("Timeout waiting for cleanup completion")
        log.info("Updating VM jobs...")
        vm.updateVmJobs()

    log.info("No more jobs")


def xml_chain(xml):
    md = metadata.Descriptor.from_xml(xml)
    with md.device(devtype="disk", name="sda") as dev:
        return dev["volumeChain"]


def parse_jobs(vm):
    """
    Parse jobs persisted in vm metadata.
    """
    root = xmlutils.fromstring(vm._dom.metadata)
    jobs = root.find("./jobs").text
    return json.loads(jobs)


def metadata_chain(xml):
    vm = xmlutils.fromstring(xml)
    nodes = vm.findall("./device/volumeChain/volumeChainNode")
    return [
        {
            "domainID": node.find("./domainID").text,
            "imageID": node.find("./imageID").text,
            "leaseOffset": int(node.find("./leaseOffset").text),
            "leasePath": node.find("./leasePath").text,
            "path": node.find("./path").text,
            "volumeID": node.find("./volumeID").text
        } for node in nodes
    ]


def fake_job(pivot=False):
    return Job(
        id="fake-job-id",
        drive=None,
        disk={"volumeID": "fake-vol"},
        top="fake-vol",
        base=None,
        bandwidth=0,
        pivot=pivot,
    )
