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

import errno
import os
import json
from contextlib import closing

import pytest

from vdsm.storage import exception as se
from vdsm.storage import managedvolume
from vdsm.storage import managedvolumedb

from . marks import requires_root


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


@pytest.fixture
def fake_lvm(monkeypatch):

    class fake_lvm:

        def __init__(self):
            self.devices_invalidated = False

        def invalidate_devices(self):
            self.devices_invalidated = True

    flvm = fake_lvm()
    monkeypatch.setattr(managedvolume, "lvm", flvm)

    return flvm


@pytest.fixture
def fake_supervdsm(monkeypatch):

    class fake_supervdsm:
        def __init__(self):
            self.udev_rules = {}

        def getProxy(self):
            return self

        def add_managed_udev_rule(self, vol_id, path):
            self.udev_rules[vol_id] = {
                "path": path,
                "triggered": False,
            }

        def trigger_managed_udev_rule(self, path):
            for rule in self.udev_rules.values():
                if rule["path"] == path:
                    rule["triggered"] = True
                    return

            raise OSError(
                errno.EINVAL, 'Could not trigger change for rule %r', rule)

        def remove_managed_udev_rule(self, vol_id):
            self.udev_rules.pop(vol_id, None)

    fsupervdsm = fake_supervdsm()
    monkeypatch.setattr(managedvolume, "supervdsm", fsupervdsm)

    return fsupervdsm


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
        managedvolume.attach_volume("sd_id", "vol_id", {})


@requires_root
def test_attach_volume_ok_iscsi(monkeypatch, fake_os_brick, tmp_db, fake_lvm,
                                fake_supervdsm):
    monkeypatch.setenv("FAKE_ATTACH_RESULT", "OK")
    connection_info = {
        "driver_volume_type": "iscsi",
        "data": {"some_info": 26}
    }
    ret = managedvolume.attach_volume(
        "fake_sd_id",
        "fake_vol_id",
        connection_info)
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
        "multipath_id": "fakemultipathid",
        "vol_id": "fake_vol_id"
    }

    assert tmp_db.get_volume("fake_vol_id") == volume_info
    assert tmp_db.owns_multipath("fakemultipathid")

    entries = fake_os_brick.log()
    assert len(entries) == 1
    assert entries[0]["action"] == "connect_volume"

    # Adding new multipath id requires filter invalidation.
    assert fake_lvm.devices_invalidated
    assert fake_supervdsm.udev_rules["fake_vol_id"]["triggered"]


@requires_root
@pytest.mark.xfail(reason='RBD monkeypatching not implemented yet')
def test_attach_volume_ok_rbd(monkeypatch, fake_os_brick, tmp_db, fake_lvm,
                              fake_supervdsm):
    monkeypatch.setenv("FAKE_ATTACH_RESULT", "OK_RBD")
    connection_info = {
        "driver_volume_type": "rbd",
        "data": {
            "name": "volumes/volume-fake"
        }}
    ret = managedvolume.attach_volume(
        "fake_sd_id",
        "fake_vol_id",
        connection_info)
    path = "/dev/rbd/volumes/volume-fake"

    assert ret["result"]["path"] == path

    volume_info = {
        "connection_info": connection_info,
        "path": path,
        "attachment": {
            "path": "/dev/fakerbd"
        },
        "vol_id": "fake_vol_id"
    }

    assert tmp_db.get_volume("fake_vol_id") == volume_info

    entries = fake_os_brick.log()
    assert len(entries) == 1
    assert entries[0]["action"] == "connect_volume"

    # RBD does not use multipath.
    assert not fake_lvm.devices_invalidated
    assert fake_supervdsm.udev_rules["fake_vol_id"]["triggered"]


@requires_root
@pytest.mark.parametrize("vol_type", ["iscsi", "fibre_channel"])
def test_attach_volume_no_multipath_id(monkeypatch, fake_os_brick, tmp_db,
                                       vol_type, fake_lvm, fake_supervdsm):
    # Simulate attaching iSCSI or FC device without multipath_id.
    monkeypatch.setenv("FAKE_ATTACH_RESULT", "NO_WWN")
    with pytest.raises(se.ManagedVolumeUnsupportedDevice):
        managedvolume.attach_volume(
            "sd_id",
            "vol_id",
            {
                "driver_volume_type": vol_type,
                "data": {"some_info": 26}
            })

    # Verify that we deatch the unsupported device.
    entries = fake_os_brick.log()
    assert len(entries) == 2
    assert entries[0]["action"] == "connect_volume"
    assert entries[1]["action"] == "disconnect_volume"

    # And remove the volume from the db.
    with pytest.raises(managedvolumedb.NotFound):
        tmp_db.get_volume("vol_id")

    # Multipath id was not added to database, so no invalidation is needed.
    assert not fake_lvm.devices_invalidated


@requires_root
def test_reattach_volume_ok_iscsi(monkeypatch, fake_os_brick, tmpdir, tmp_db,
                                  fake_lvm, fake_supervdsm):
    monkeypatch.setenv("FAKE_ATTACH_RESULT", "OK")
    monkeypatch.setattr(managedvolume, "DEV_MAPPER", str(tmpdir))
    tmpdir.join("fakemultipathid").write("")
    connection_info = {
        "driver_volume_type": "iscsi",
        "data": {"some_info": 26}
    }
    managedvolume.attach_volume("fake_sd_id", "fake_vol_id", connection_info)

    # Attaching invalidates the filter, reset.
    fake_lvm.devices_invalidated = False

    with pytest.raises(se.ManagedVolumeAlreadyAttached):
        managedvolume.attach_volume(
            "fake_sd_id",
            "fake_vol_id",
            connection_info)

    # Device still owned by managed volume.
    assert tmp_db.owns_multipath("fakemultipathid")

    entries = fake_os_brick.log()
    assert len(entries) == 1
    assert entries[0]["action"] == "connect_volume"

    # Volume already attached, no need to invalidated filter.
    assert not fake_lvm.devices_invalidated


@requires_root
def test_attach_volume_fail_update(monkeypatch, fake_os_brick, tmpdir, tmp_db,
                                   fake_lvm, fake_supervdsm):
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
        managedvolume.attach_volume(
            "fake_sd_id",
            "fake_vol_id",
            connection_info)

    entries = fake_os_brick.log()
    assert len(entries) == 2
    assert entries[0]["action"] == "connect_volume"
    assert entries[1]["action"] == "disconnect_volume"

    # Multipath id not added, no need to invalidated filter.
    assert not fake_lvm.devices_invalidated


@requires_root
def test_reattach_volume_other_connection(monkeypatch, fake_os_brick, tmp_db,
                                          fake_lvm, fake_supervdsm):
    monkeypatch.setenv("FAKE_ATTACH_RESULT", "OK")
    connection_info = {
        "driver_volume_type": "iscsi",
        "data": {"some_info": 26}
    }
    managedvolume.attach_volume("fake_sd_id", "fake_vol_id", connection_info)

    # Attaching invalidates the filter, reset.
    fake_lvm.devices_invalidated = False

    other_connection_info = {
        "driver_volume_type": "iscsi",
        "data": {"some_info": 99}
    }

    with pytest.raises(se.ManagedVolumeConnectionMismatch):
        managedvolume.attach_volume(
            "fake_sd_id",
            "fake_vol_id",
            other_connection_info)

    entries = fake_os_brick.log()
    assert len(entries) == 1
    assert entries[0]["action"] == "connect_volume"

    # No multipath id added, no need to invalidated filter.
    assert not fake_lvm.devices_invalidated


@requires_root
def test_detach_volume_iscsi_not_attached(monkeypatch, fake_os_brick, tmp_db,
                                          fake_lvm, fake_supervdsm):
    monkeypatch.setenv("FAKE_ATTACH_RESULT", "OK")
    connection_info = {
        "driver_volume_type": "iscsi",
        "data": {"some_info": 26}
    }
    managedvolume.attach_volume("fake_sd_id", "fake_vol_id", connection_info)

    # Attaching invalidates the filter, reset.
    fake_lvm.devices_invalidated = False

    managedvolume.detach_volume("fake_vol_id")

    with pytest.raises(managedvolumedb.NotFound):
        tmp_db.get_volume("fake_vol_id")

    entries = fake_os_brick.log()
    assert len(entries) == 1
    assert entries[0]["action"] == "connect_volume"

    # No multipath id added, no need to invalidated filter.
    assert not fake_lvm.devices_invalidated


@requires_root
def test_detach_volume_not_installed(monkeypatch, fake_os_brick, tmp_db,
                                     fake_lvm):
    # Simulate missing os_brick.
    monkeypatch.setattr(managedvolume, "os_brick", None)

    # Attaching invalidates the filter, reset.
    fake_lvm.devices_invalidated = False

    with pytest.raises(se.ManagedVolumeNotSupported):
        managedvolume.detach_volume("vol_id")

    # No multipath id added, no need to invalidated filter.
    assert not fake_lvm.devices_invalidated


@requires_root
def test_detach_not_in_db(monkeypatch, fake_os_brick, tmp_db, fake_lvm,
                          fake_supervdsm):
    managedvolume.detach_volume("fake_vol_id")

    # Attaching invalidates the filter, reset.
    fake_lvm.devices_invalidated = False

    with pytest.raises(managedvolumedb.NotFound):
        tmp_db.get_volume("fake_vol_id")
    assert [] == fake_os_brick.log()

    # No multipath id added, no need to invalidated filter.
    assert not fake_lvm.devices_invalidated


@requires_root
def test_detach_volume_iscsi_attached(monkeypatch, fake_os_brick, tmpdir,
                                      tmp_db, fake_lvm, fake_supervdsm):
    monkeypatch.setenv("FAKE_ATTACH_RESULT", "OK")
    monkeypatch.setattr(managedvolume, "DEV_MAPPER", str(tmpdir))
    connection_info = {
        "driver_volume_type": "iscsi",
        "data": {"some_info": 26}
    }
    managedvolume.attach_volume("fake_sd_id", "fake_vol_id", connection_info)

    # Attaching invalidates the filter, reset.
    fake_lvm.devices_invalidated = False

    tmpdir.join("fakemultipathid").write("")
    managedvolume.detach_volume("fake_vol_id")

    entries = fake_os_brick.log()
    assert len(entries) == 2
    assert entries[0]["action"] == "connect_volume"
    assert entries[1]["action"] == "disconnect_volume"

    with pytest.raises(managedvolumedb.NotFound):
        tmp_db.get_volume("fake_vol_id")

    # Device not owned by managed volume.
    assert not tmp_db.owns_multipath("fakemultipathid")

    # No multipath id added, no need to invalidated filter.
    assert not fake_lvm.devices_invalidated


def test_volume_info_not_found(tmp_db):
    assert [] == managedvolume.volumes_info(["fake_vol_id"])["result"]
    assert [] == managedvolume.volumes_info()["result"]


def test_volume_info_path_not_exists(tmp_db):
    vol_info = {
        "exists": False,
        "connection_info": {"connection": 1},
        "path": "/dev/mapper/fakemultipathid",
        "attachment": {"attachment": 1},
        "vol_id": "vol_id"
    }

    tmp_db.add_volume("vol_id", vol_info["connection_info"])
    tmp_db.update_volume(vol_info["vol_id"], vol_info["path"],
                         vol_info["attachment"], "fakemultipathid")

    assert managedvolume.volumes_info(["vol_id"])["result"] == [vol_info]


def test_volume_info_path_exists(tmpdir, tmp_db):
    path = tmpdir.join("image")
    path.write("")

    vol_info = {
        "exists": True,
        "connection_info": {"connection": 1},
        "path": str(path),
        "attachment": {"attachment": 1},
        "vol_id": "vol_id"
    }

    tmp_db.add_volume("vol_id", vol_info["connection_info"])
    tmp_db.update_volume(vol_info["vol_id"], vol_info["path"],
                         vol_info["attachment"], "fakemultipathid")

    assert managedvolume.volumes_info(["vol_id"])["result"] == [vol_info]


def test_volume_info_all(tmp_db):
    expected = [{"vol_id": "vol-id-1",
                 "connection_info": {"connection": 1}},
                {"vol_id": "vol-id-2",
                 "exists": False,
                 "connection_info": {"connection": 2},
                 "path": "/dev/mapper/fakemultipathid",
                 "attachment": {"attachment": 2}}]

    db = managedvolumedb.open()
    with closing(db):
        for vol in expected:
            db.add_volume(vol["vol_id"], vol["connection_info"])
            if "path" in vol:
                db.update_volume(vol["vol_id"], vol["path"], vol["attachment"],
                                 "fakemultipathid")

    assert expected == managedvolume.volumes_info()["result"]
