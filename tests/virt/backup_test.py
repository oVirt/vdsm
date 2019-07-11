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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import collections
import libvirt
import os
import pytest

from fakelib import FakeLogger
from testlib import make_uuid
from virt.fakedomainadapter import FakeDomainAdapter

from vdsm.common import exception
from vdsm.common import nbdutils
from vdsm.common.xmlutils import indented

from vdsm.storage import hsm
from vdsm.storage import transientdisk
from vdsm.storage.dispatcher import Dispatcher

from vdsm.virt import backup

import vmfakelib as fake

requires_backup_support = pytest.mark.skipif(
    not backup.backup_enabled,
    reason="libvirt does not support backup")


class FakeDrive(object):

    def __init__(self, name, imageID, path=''):
        self.name = name
        self.imageID = imageID
        self.diskType = 'file'
        self.path = path
        self.format = 'cow'
        self.domainID = 'domain_id'


class FakeHSM(hsm.HSM):

    def __init__(self):
        self._ready = True

    @property
    def ready(self):
        return self._ready


class FakeClientIF(object):

    def __init__(self):
        self.irs = Dispatcher(FakeHSM())


class FakeVm(object):

    def __init__(self):
        self.id = "vm_id"
        self.log = FakeLogger()
        self.cif = FakeClientIF()

    def findDriveByUUIDs(self, disk):
        return FAKE_DRIVES[disk['imageID']]

    def find_device_by_name_or_path(self, disk_name):
        for fake_drive in FAKE_DRIVES.values():
            if fake_drive.name == disk_name:
                return fake_drive

        raise LookupError("Disk %s not found" % disk_name)


IMAGE_1_UUID = make_uuid()
IMAGE_2_UUID = make_uuid()

FAKE_DRIVES = {
    IMAGE_1_UUID:
        FakeDrive(name="sda", imageID=IMAGE_1_UUID, path="/path/to/backing1"),
    IMAGE_2_UUID:
        FakeDrive(name="vda", imageID=IMAGE_2_UUID, path="/path/to/backing2"),
}

FAKE_SCRATCH_DISKS = {
    "sda": "/path/to/scratch_sda",
    "vda": "/path/to/scratch_vda",
}


@pytest.fixture
def tmp_backupdir(tmpdir, monkeypatch):
    path = str(tmpdir.join("backup"))
    monkeypatch.setattr(backup, 'P_BACKUP', path)


@pytest.fixture
def tmp_basedir(tmpdir, monkeypatch):
    path = str(tmpdir.join("transient_disks"))
    monkeypatch.setattr(transientdisk, 'P_TRANSIENT_DISKS', path)


def test_backup_xml(tmp_backupdir):
    backup_id = 'backup_id'

    # drives must be sorted for the disks to appear
    # each time in the same order in the backup XML
    drives = collections.OrderedDict()
    drives["img-id-1"] = FakeDrive("sda", "img-id-1")
    drives["img-id-2"] = FakeDrive("vda", "img-id-2")

    socket_path = os.path.join(backup.P_BACKUP, backup_id)
    addr = nbdutils.UnixAddress(socket_path)

    backup_xml = backup.create_backup_xml(
        addr, drives, FAKE_SCRATCH_DISKS)

    expected_xml = """
        <domainbackup mode='pull'>
            <server transport='unix' socket='{}'/>
            <disks>
                <disk name='sda' type='file'>
                    <scratch file='/path/to/scratch_sda'>
                        <seclabel model="dac" relabel="no"/>
                    </scratch>
                </disk>
                <disk name='vda' type='file'>
                    <scratch file='/path/to/scratch_vda'>
                        <seclabel model="dac" relabel="no"/>
                    </scratch>
                </disk>
            </disks>
        </domainbackup>
        """.format(socket_path)
    assert indented(expected_xml) == indented(backup_xml)


@requires_backup_support
def test_start_stop_backup(tmp_backupdir, tmp_basedir):
    backup_id = 'backup_id'
    vm = FakeVm()
    dom = FakeDomainAdapter()

    fake_disks = create_fake_disks()
    config = {
        'backup_id': backup_id,
        'disks': fake_disks
    }

    res = backup.start_backup(vm, dom, config)
    assert dom.backing_up

    verify_scratch_disks_exists(vm)

    result_disks = res['result']['disks']
    verify_backup_urls(backup_id, result_disks)

    backup.stop_backup(vm, dom, backup_id)
    assert not dom.backing_up

    verify_scratch_disks_removed(vm)


def test_start_backup_disk_not_found():
    vm = FakeVm()
    dom = FakeDomainAdapter()

    fake_disks = create_fake_disks()
    fake_disks.append({
        'domainID': make_uuid(),
        'imageID': make_uuid(),
        'volumeID': make_uuid()})

    config = {
        'backup_id': 'backup_id',
        'disks': fake_disks
    }

    with pytest.raises(exception.BackupError):
        backup.start_backup(vm, dom, config)

    assert not dom.backing_up
    verify_scratch_disks_removed(vm)


@requires_backup_support
def test_backup_begin_failed(tmp_backupdir, tmp_basedir):
    backup_id = 'backup_id'
    vm = FakeVm()
    dom = FakeDomainAdapter()
    dom.errors["backupBegin"] = fake.libvirt_error(
        [libvirt.VIR_ERR_INTERNAL_ERROR], "Fake libvirt error")

    fake_disks = create_fake_disks()

    config = {
        'backup_id': backup_id,
        'disks': fake_disks
    }

    with pytest.raises(exception.BackupError):
        backup.start_backup(vm, dom, config)

    verify_scratch_disks_removed(vm)


@requires_backup_support
def test_stop_backup_failed(tmp_backupdir, tmp_basedir):
    backup_id = 'backup_id'
    vm = FakeVm()
    dom = FakeDomainAdapter()
    dom.errors["abortJob"] = fake.libvirt_error(
        [libvirt.VIR_ERR_INTERNAL_ERROR], "Fake libvirt error")

    fake_disks = create_fake_disks()

    config = {
        'backup_id': backup_id,
        'disks': fake_disks
    }

    res = backup.start_backup(vm, dom, config)

    verify_scratch_disks_exists(vm)

    result_disks = res['result']['disks']
    verify_backup_urls(backup_id, result_disks)

    with pytest.raises(exception.BackupError):
        backup.stop_backup(vm, dom, backup_id)

    # Failed to stop, backup still alive
    assert dom.backing_up

    # verify scratch disks weren't removed
    verify_scratch_disks_exists(vm)


@requires_backup_support
def test_stop_non_existing_backup():
    vm = FakeVm()
    dom = FakeDomainAdapter()
    dom.errors["backupGetXMLDesc"] = fake.libvirt_error(
        [libvirt.VIR_ERR_NO_DOMAIN_BACKUP], "Fake libvirt error")

    # test that nothing is raised when stopping non-existing backup
    backup.stop_backup(vm, dom, 'backup_id')


@requires_backup_support
def test_backup_info(tmp_backupdir, tmp_basedir):
    backup_id = 'backup_id'
    vm = FakeVm()
    expected_xml = """
        <domainbackup mode='pull'>
          <server transport='unix' socket='{}'/>
          <disks>
            <disk name='sda' backup='yes' type='file' exportname='sda'>
                <driver type='qcow2'/>
                <scratch file='/path/to/scratch_sda'>
                    <seclabel model='dac' relabel='no'/>
                </scratch>
            </disk>
            <disk name='vda' backup='yes' type='file' exportname='vda'>
                <driver type='qcow2'/>
                <scratch file='/path/to/scratch_vda'>
                    <seclabel model="dac" relabel="no"/>
                </scratch>
            </disk>
            <disk name='hdc' backup='no'/>
          </disks>
        </domainbackup>
        """.format(os.path.join(backup.P_BACKUP, backup_id))
    dom = FakeDomainAdapter(expected_xml)

    fake_disks = create_fake_disks()
    config = {
        'backup_id': backup_id,
        'disks': fake_disks
    }
    res = backup.start_backup(vm, dom, config)
    backup_info = backup.backup_info(vm, dom, backup_id)
    assert res['result']['disks'] == backup_info['result']['disks']


@requires_backup_support
def test_backup_info_no_backup_running():
    vm = FakeVm()
    dom = FakeDomainAdapter()
    dom.errors["backupGetXMLDesc"] = fake.libvirt_error(
        [libvirt.VIR_ERR_NO_DOMAIN_BACKUP], "Fake libvirt error")

    with pytest.raises(exception.NoSuchBackupError):
        backup.backup_info(vm, dom, "backup_id")


@requires_backup_support
def test_backup_info_get_xml_desc_failed():
    vm = FakeVm()
    dom = FakeDomainAdapter()
    dom.errors["backupGetXMLDesc"] = fake.libvirt_error(
        [libvirt.VIR_ERR_INTERNAL_ERROR], "Fakse libvirt error")

    with pytest.raises(exception.BackupError):
        backup.backup_info(vm, dom, "backup_id")


@requires_backup_support
def test_fail_parse_backup_xml(tmp_backupdir, tmp_basedir):
    backup_id = 'backup_id'
    vm = FakeVm()
    INVALID_BACKUP_XML = """
        <domainbackup mode='pull'>
            <disks/>
        </domainbackup>
        """
    dom = FakeDomainAdapter(INVALID_BACKUP_XML)

    fake_disks = create_fake_disks()
    config = {
        'backup_id': backup_id,
        'disks': fake_disks
    }
    backup.start_backup(vm, dom, config)

    with pytest.raises(exception.BackupError):
        backup.backup_info(vm, dom, backup_id)


def verify_scratch_disks_exists(vm):
    res = vm.cif.irs.list_transient_disks(vm.id)
    assert res["status"]["code"] == 0

    scratch_disks = ["backup_id." + drive.name
                     for drive in FAKE_DRIVES.values()]
    assert sorted(res["result"]) == sorted(scratch_disks)


def verify_backup_urls(backup_id, result_disks):
    for image_id, drive in FAKE_DRIVES.items():
        socket_path = os.path.join(backup.P_BACKUP, backup_id)
        exp_addr = nbdutils.UnixAddress(socket_path).url(drive.name)
        assert result_disks[image_id] == exp_addr


def verify_scratch_disks_removed(vm):
    res = vm.cif.irs.list_transient_disks(vm.id)
    assert res['status']['code'] == 0
    assert res['result'] == []


def create_fake_disks():
    fake_disks = []
    for img_id in FAKE_DRIVES:
        fake_disks.append({
            'domainID': make_uuid(),
            'imageID': img_id,
            'volumeID': make_uuid()})
    return fake_disks
