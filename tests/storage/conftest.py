#
# Copyright 2019 Red Hat, Inc.
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

"""
Common fixtures that can be used without importing anything.
"""

from __future__ import absolute_import
from __future__ import division

import os

from contextlib import closing

import pytest

from vdsm.common import threadlocal
from vdsm.storage import blockSD
from vdsm.storage import clusterlock
from vdsm.storage import constants as sc
from vdsm.storage import fallocate
from vdsm.storage import fileSD
from vdsm.storage import fileVolume
from vdsm.storage import lvm
from vdsm.storage import managedvolumedb
from vdsm.storage import multipath
from vdsm.storage import outOfProcess as oop
from vdsm.storage import task
from vdsm.storage.sdc import sdCache

from .fakesanlock import FakeSanlock
from . import tmpfs
from . import tmprepo
from . import tmpstorage


@pytest.fixture
def tmp_repo(tmpdir, monkeypatch, tmp_fs):
    """
    Provide a temporary repo directory and patch vsdm to use it instead of
    /rhev/data-center.
    """
    repo = tmprepo.TemporaryRepo(tmpdir, tmp_fs)

    # Patch repo directory.
    monkeypatch.setattr(sc, "REPO_DATA_CENTER", repo.path)
    monkeypatch.setattr(sc, "REPO_MOUNT_DIR", repo.mnt_dir)

    # Invalidate sdCache so stale data from previous test will affect
    # this test.
    sdCache.refresh()
    sdCache.knownSDs.clear()

    try:
        yield repo
    finally:
        # ioprocess is typically invoked from tests using tmp_repo. This
        # terminate ioprocess instances, avoiding thread and process leaks in
        # tests, and errors in __del__ during test shutdown.
        oop.stop()

        # Invalidate sdCache so stale data from this test will affect
        # the next test.
        sdCache.refresh()
        sdCache.knownSDs.clear()


@pytest.fixture
def tmp_fs(tmp_storage):
    """
    Provides a temporary file system created on provided device. Contains also
    support for mounting newly created FS.
    """
    fs = tmpfs.TemporaryFS(tmp_storage)
    with closing(fs):
        yield fs


@pytest.fixture
def tmp_lvm():
    lvm.set_read_only(True)
    lvm.invalidateCache()
    lvm._on_x86_64.invalidate()
    yield
    lvm.set_read_only(True)
    lvm.invalidateCache()
    lvm._on_x86_64.invalidate()


@pytest.fixture
def tmp_storage(monkeypatch, tmpdir, tmp_lvm):
    """
    Provide a temporary storage for creating temporary block devices, and patch
    vsdm to use it instead of multipath device.
    """
    storage = tmpstorage.TemporaryStorage(str(tmpdir))

    # Get devices from our temporary storage instead of multipath.
    monkeypatch.setattr(multipath, "getMPDevNamesIter", storage.devices)

    # Use custom /run/vdsm/storage directory, used to keep symlinks to active
    # lvs.
    storage_dir = str(tmpdir.join("storage"))
    os.mkdir(storage_dir)
    monkeypatch.setattr(sc, "P_VDSM_STORAGE", storage_dir)

    with closing(storage):
        yield storage


@pytest.fixture
def fake_access(monkeypatch):
    """
    Fake access checks used in file based storage using supervdsm.

    Returned object has a "allowed" attribute set to True to make access check
    succeed. To make access check fail, set it to False.
    """
    class fake_access:
        allowed = True

    fa = fake_access()
    monkeypatch.setattr(fileSD, "validateDirAccess", lambda path: fa.allowed)
    return fa


@pytest.fixture
def fake_task(monkeypatch):
    """
    Create fake task, expected in various places in the code. In the real code
    a task is created for every HSM public call by the dispatcher.
    """
    monkeypatch.setattr(threadlocal.vars, 'task', task.Task("fake-task-id"))


@pytest.fixture
def fake_rescan(monkeypatch):
    """
    Fake rescanning of devices. Do nothing instead.
    """
    def rescan():
        pass

    monkeypatch.setattr(multipath, "rescan", rescan)


@pytest.fixture
def tmp_db(monkeypatch, tmpdir):
    """
    Create managed volume database in temporal directory.
    """
    db_file = str(tmpdir.join("managedvolumes.db"))
    monkeypatch.setattr(managedvolumedb, "DB_FILE", db_file)
    managedvolumedb.create_db()


@pytest.fixture
def fake_sanlock(monkeypatch):
    """
    Create fake sanlock which mimics sanlock functionality.
    """
    fs = FakeSanlock()
    monkeypatch.setattr(clusterlock, "sanlock", fs)
    monkeypatch.setattr(blockSD, "sanlock", fs)
    monkeypatch.setattr(fileVolume, "sanlock", fs)
    return fs


@pytest.fixture
def local_fallocate(monkeypatch):
    monkeypatch.setattr(fallocate, '_FALLOCATE', '../helpers/fallocate')
