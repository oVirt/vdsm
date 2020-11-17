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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license

from __future__ import absolute_import
from __future__ import division

import os
import stat

import pytest

from vdsm.common.units import MiB

from vdsm.storage import constants as sc
from vdsm.storage import exception as se
from vdsm.storage import qemuimg
from vdsm.storage import transientdisk


@pytest.fixture
def tmp_basedir(monkeypatch, tmpdir):
    monkeypatch.setattr(transientdisk, 'P_TRANSIENT_DISKS', str(tmpdir))


def test_add_transient_disk(tmp_basedir):
    owner_name = 'vm-id'
    disk_name = 'sda'

    res = transientdisk.create_disk(
        owner_name=owner_name,
        disk_name=disk_name,
        size=10 * MiB
    )

    disk_path = res['path']
    disk_info = qemuimg.info(disk_path)
    assert disk_info['format'] == 'qcow2'
    assert disk_info['format-specific']['data']['compat'] == '1.1'
    assert disk_info['virtual-size'] == 10 * MiB

    assert "backing-filename" not in disk_info

    permissions = stat.S_IMODE(os.stat(disk_path).st_mode)
    assert oct(permissions) == oct(sc.FILE_VOLUME_PERMISSIONS)


def test_add_transient_disk_with_backing(tmp_basedir, tmpdir):
    src = str(tmpdir.join("src.qcow2"))
    qemuimg.create(src, size=10 * MiB, format="qcow2", qcow2Compat="1.1").run()

    res = transientdisk.create_disk(
        "backup-id",
        "overlay.qcow2",
        backing=src,
        backing_format="qcow2")

    disk_path = res['path']
    disk_info = qemuimg.info(disk_path)
    assert disk_info['format'] == "qcow2"
    assert disk_info['format-specific']['data']['compat'] == "1.1"
    assert disk_info['virtual-size'] == 10 * MiB
    assert disk_info['backing-filename'] == src
    assert disk_info['backing-filename-format'] == "qcow2"

    permissions = stat.S_IMODE(os.stat(disk_path).st_mode)
    assert oct(permissions) == oct(sc.FILE_VOLUME_PERMISSIONS)


def test_remove_transient_disk(tmpdir, tmp_basedir):
    owner_name = 'vm-id'
    disk1_name = 'sda'
    disk2_name = 'sdb'

    for disk_name in [disk1_name, disk2_name]:
        transientdisk.create_disk(
            owner_name=owner_name,
            disk_name=disk_name,
            size=10 * MiB
        )

    transientdisk.remove_disk(owner_name, disk1_name)
    # owner dir should remain
    assert transientdisk.list_disks(owner_name) == [disk2_name]

    transientdisk.remove_disk(owner_name, disk2_name)
    # owner dir should be removed now
    assert transientdisk.list_disks(owner_name) == []


def test_add_existing_transient_disk(tmp_basedir):
    owner_name = 'vm-id'
    disk_name = 'sda'

    def create_disk():
        return transientdisk.create_disk(
            owner_name=owner_name,
            disk_name=disk_name,
            size=10 * MiB
        )

    create_disk()
    with pytest.raises(se.TransientDiskAlreadyExists):
        create_disk()

    assert transientdisk.list_disks(owner_name) == [disk_name]


def test_remove_disk_not_exists(tmp_basedir):
    owner_name = 'vm-id'
    disk1_name = 'sda'
    disk2_name = 'sdb'

    transientdisk.create_disk(
        owner_name=owner_name,
        disk_name=disk1_name,
        size=10 * MiB
    )
    # removal of disk that isn't exists should not
    # raise any exception
    transientdisk.remove_disk(owner_name, disk2_name)
    assert transientdisk.list_disks(owner_name) == [disk1_name]


def test_remove_disk_and_dir_not_exists(tmp_basedir):
    owner_name = 'vm-id'
    disk_name = 'sda'

    # removal of disk and directory that aren't exists
    # should not raise any exception
    transientdisk.remove_disk(owner_name, disk_name)


def test_list_disks(tmp_basedir):
    owner_name = 'vm-id'
    disk1_name = 'sda'
    disk2_name = 'sdb'

    for disk_name in [disk1_name, disk2_name]:
        res = transientdisk.create_disk(
            owner_name=owner_name,
            disk_name=disk_name,
            size=10 * MiB
        )

        actual_path = res['path']
        assert os.path.isfile(actual_path)

    disks_list = transientdisk.list_disks(owner_name)
    assert sorted(disks_list) == [disk1_name, disk2_name]


def test_list_disks_no_dir(tmp_basedir):
    owner_name = 'vm-id'
    disks_list = transientdisk.list_disks(owner_name)

    assert disks_list == []
