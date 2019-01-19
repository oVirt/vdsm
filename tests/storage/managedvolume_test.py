#
# Copyright 2009-2019 Red Hat, Inc.
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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#

from __future__ import absolute_import
from __future__ import division

import os
import json
from contextlib import closing

import pytest

from vdsm.storage import exception as se
from vdsm.storage import managedvolume
from vdsm.storage import managedvolumedb


requires_root = pytest.mark.skipif(
    os.geteuid() != 0, reason="requires root")


@pytest.fixture
def tmp_db(tmpdir, monkeypatch):
    db_file = str(tmpdir.join("managedvolumes.db"))
    monkeypatch.setattr(managedvolumedb, "DB_FILE", db_file)
    managedvolumedb.create_db()
    db = managedvolumedb.open()
    with closing(db):
        yield db


@pytest.fixture
def fake_os_brick(monkeypatch, tmpdir):
    # os_brick log
    log_path = tmpdir.join("os_brick.log")
    log_path.write("")
    monkeypatch.setenv("FAKE_OS_BRICK_LOG", str(log_path))

    monkeypatch.setattr(
        managedvolume, 'HELPER', "../lib/vdsm/storage/managedvolume-helper")
    os_brick_dir = os.path.abspath("storage/fake_os_brick")
    monkeypatch.setenv("PYTHONPATH", os_brick_dir, prepend=":")

    # os_brick may not be available yet on developers machines. Make sure we
    # test with our fake os_brick.
    monkeypatch.setattr(managedvolume, "os_brick", object())

    class fake_os_brick:
        def log(self):
            with open(str(log_path)) as f:
                return [json.loads(e) for e in f.readlines()]

    return fake_os_brick()


@requires_root
def test_connector_info_not_installed(monkeypatch):
    # Simulate missing os_brick.
    monkeypatch.setattr(managedvolume, "os_brick", None)
    with pytest.raises(se.ManagedVolumeNotSupported):
        managedvolume.connector_info()


@requires_root
def test_connector_info_ok(monkeypatch, fake_os_brick):
    monkeypatch.setenv("FAKE_CONNECTOR_INFO_RESULT", "OK")
    assert managedvolume.connector_info() == {"multipath": True}


@requires_root
def test_connector_info_fail(monkeypatch, fake_os_brick):
    monkeypatch.setenv("FAKE_CONNECTOR_INFO_RESULT", "FAIL")
    with pytest.raises(se.ManagedVolumeHelperFailed):
        managedvolume.connector_info()


@requires_root
def test_connector_info_fail_json(monkeypatch, fake_os_brick):
    monkeypatch.setenv("FAKE_CONNECTOR_INFO_RESULT", "FAIL_JSON")
    with pytest.raises(se.ManagedVolumeHelperFailed):
        managedvolume.connector_info()


@requires_root
def test_connector_info_raise(monkeypatch, fake_os_brick):
    monkeypatch.setenv("FAKE_CONNECTOR_INFO_RESULT", "RAISE")
    with pytest.raises(se.ManagedVolumeHelperFailed) as e:
        managedvolume.connector_info()
    assert "error message from os_brick" in str(e.value)


@requires_root
def test_attach_volume_not_installed_attach(monkeypatch):
    # Simulate missing os_brick.
    monkeypatch.setattr(managedvolume, "os_brick", None)
    with pytest.raises(se.ManagedVolumeNotSupported):
        managedvolume.attach_volume("vol_id", {})


@requires_root
def test_attach_volume_ok_iscsi(monkeypatch, fake_os_brick, tmp_db):
    monkeypatch.setenv("FAKE_ATTACH_RESULT", "OK")
    connection_info = {
        "driver_volume_type": "iscsi",
        "data": {"some_info": 26}
    }
    ret = managedvolume.attach_volume("fake_vol_id", connection_info)
    path = "/dev/mapper/fakemultipathid"

    assert ret["result"]["path"] == path

    volume_info = {
        "connection_info": connection_info,
        "path": path,
        "attachment": {
            "path": "/dev/fakesda",
            "scsi_wwn": "fakewwn",
            "multipath_id": "fakemultipathid"
        },
        "multipath_id": "fakemultipathid"
    }

    assert tmp_db.get_volume("fake_vol_id") == volume_info

    entries = fake_os_brick.log()
    assert len(entries) == 1
    assert entries[0]["action"] == "connect_volume"


@requires_root
def test_attach_volume_ok_rbd(monkeypatch, fake_os_brick, tmp_db):
    monkeypatch.setenv("FAKE_ATTACH_RESULT", "OK_RBD")
    connection_info = {
        "driver_volume_type": "rbd",
        "data": {
            "name": "volumes/volume-fake"
        }}
    ret = managedvolume.attach_volume("fake_vol_id", connection_info)
    path = "/dev/rbd/volumes/volume-fake"

    assert ret["result"]["path"] == path

    volume_info = {
        "connection_info": connection_info,
        "path": path,
        "attachment": {
            "path": "/dev/fakerbd"
        }
    }

    assert tmp_db.get_volume("fake_vol_id") == volume_info

    entries = fake_os_brick.log()
    assert len(entries) == 1
    assert entries[0]["action"] == "connect_volume"


@requires_root
def test_attach_volume_ok_other(monkeypatch, fake_os_brick, tmp_db):
    monkeypatch.setenv("FAKE_ATTACH_RESULT", "NO_WWN")
    connection_info = {
        "driver_volume_type": "other",
        "data": {
            "param1": "value1"
        }
    }
    ret = managedvolume.attach_volume("other_vol_id", connection_info)
    path = "/dev/fakesda"

    assert ret["result"]["path"] == path

    volume_info = {
        "connection_info": connection_info,
        "path": path,
        "attachment": {
            "path": "/dev/fakesda"
        }
    }

    assert tmp_db.get_volume("other_vol_id") == volume_info

    entries = fake_os_brick.log()
    assert len(entries) == 1
    assert entries[0]["action"] == "connect_volume"


@requires_root
@pytest.mark.parametrize("vol_type", ["iscsi", "fibre_channel"])
def test_attach_volume_no_multipath_id(monkeypatch, fake_os_brick, tmp_db,
                                       vol_type):
    # Simulate attaching iSCSI or FC device without multipath_id.
    monkeypatch.setenv("FAKE_ATTACH_RESULT", "NO_WWN")
    with pytest.raises(se.ManagedVolumeUnsupportedDevice):
        managedvolume.attach_volume("vol_id", {
            "driver_volume_type": vol_type,
            "data": {"some_info": 26}})

    # Verify that we deatch the unsupported device.
    entries = fake_os_brick.log()
    assert len(entries) == 2
    assert entries[0]["action"] == "connect_volume"
    assert entries[1]["action"] == "disconnect_volume"

    # And remove the volume from the db.
    with pytest.raises(managedvolumedb.NotFound):
        tmp_db.get_volume("vol_id")


@requires_root
def test_reattach_volume_ok_iscsi(monkeypatch, fake_os_brick, tmpdir, tmp_db):
    monkeypatch.setenv("FAKE_ATTACH_RESULT", "OK")
    monkeypatch.setattr(managedvolume, "DEV_MAPPER", str(tmpdir))
    tmpdir.join("fakemultipathid").write("")
    connection_info = {
        "driver_volume_type": "iscsi",
        "data": {"some_info": 26}
    }
    managedvolume.attach_volume("fake_vol_id", connection_info)

    with pytest.raises(se.ManagedVolumeAlreadyAttached):
        managedvolume.attach_volume("fake_vol_id", connection_info)

    entries = fake_os_brick.log()
    assert len(entries) == 1
    assert entries[0]["action"] == "connect_volume"


@requires_root
def test_attach_volume_fail_update(monkeypatch, fake_os_brick, tmpdir, tmp_db):
    monkeypatch.setenv("FAKE_ATTACH_RESULT", "OK")
    monkeypatch.setattr(managedvolume, "DEV_MAPPER", str(tmpdir))
    tmpdir.join("fakemultipathid").write("")
    connection_info = {
        "driver_volume_type": "iscsi",
        "data": {"some_info": 26}
    }

    def raise_error(*args, **kargs):
        raise RuntimeError

    monkeypatch.setattr(managedvolumedb.DB, "update_volume", raise_error)

    with pytest.raises(RuntimeError):
        managedvolume.attach_volume("fake_vol_id", connection_info)

    entries = fake_os_brick.log()
    assert len(entries) == 2
    assert entries[0]["action"] == "connect_volume"
    assert entries[1]["action"] == "disconnect_volume"


@requires_root
def test_reattach_volume_other_connection(monkeypatch, fake_os_brick, tmp_db):
    monkeypatch.setenv("FAKE_ATTACH_RESULT", "OK")
    connection_info = {
        "driver_volume_type": "iscsi",
        "data": {"some_info": 26}
    }
    managedvolume.attach_volume("fake_vol_id", connection_info)

    other_connection_info = {
        "driver_volume_type": "iscsi",
        "data": {"some_info": 99}
    }

    with pytest.raises(se.ManagedVolumeConnectionMismatch):
        managedvolume.attach_volume("fake_vol_id", other_connection_info)

    entries = fake_os_brick.log()
    assert len(entries) == 1
    assert entries[0]["action"] == "connect_volume"


@requires_root
def test_detach_volume_iscsi_not_attached(monkeypatch, fake_os_brick, tmp_db):
    monkeypatch.setenv("FAKE_ATTACH_RESULT", "OK")
    connection_info = {
        "driver_volume_type": "iscsi",
        "data": {"some_info": 26}
    }
    managedvolume.attach_volume("fake_vol_id", connection_info)
    managedvolume.detach_volume("fake_vol_id")

    with pytest.raises(managedvolumedb.NotFound):
        tmp_db.get_volume("fake_vol_id")

    entries = fake_os_brick.log()
    assert len(entries) == 1
    assert entries[0]["action"] == "connect_volume"


@requires_root
def test_detach_volume_not_installed(monkeypatch, fake_os_brick, tmp_db):
    # Simulate missing os_brick.
    monkeypatch.setattr(managedvolume, "os_brick", None)
    with pytest.raises(se.ManagedVolumeNotSupported):
        managedvolume.detach_volume("vol_id")


@requires_root
def test_detach_not_in_db(monkeypatch, fake_os_brick, tmp_db):
    managedvolume.detach_volume("fake_vol_id")
    with pytest.raises(managedvolumedb.NotFound):
        tmp_db.get_volume("fake_vol_id")
    assert [] == fake_os_brick.log()


@requires_root
def test_detach_volume_iscsi_attached(monkeypatch, fake_os_brick, tmpdir,
                                      tmp_db):
    monkeypatch.setenv("FAKE_ATTACH_RESULT", "OK")
    monkeypatch.setattr(managedvolume, "DEV_MAPPER", str(tmpdir))
    connection_info = {
        "driver_volume_type": "iscsi",
        "data": {"some_info": 26}
    }
    managedvolume.attach_volume("fake_vol_id", connection_info)
    tmpdir.join("fakemultipathid").write("")
    managedvolume.detach_volume("fake_vol_id")

    entries = fake_os_brick.log()
    assert len(entries) == 2
    assert entries[0]["action"] == "connect_volume"
    assert entries[1]["action"] == "disconnect_volume"

    with pytest.raises(managedvolumedb.NotFound):
        tmp_db.get_volume("fake_vol_id")
