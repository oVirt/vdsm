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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA
# 02110-1301  USA
#
# Refer to the README and COPYING files for full details of the license
#

import logging
import uuid

import libvirt
import pytest

from vdsm import clientIF
from vdsm.common import exception
from vdsm.virt import vm
from vdsm.virt.vmdevices import drivename

from testlib import normalized

from . import vmfakelib as fake

EMPTY_CD_METADATA_XML = "<ovirt-vm:device devtype='cdrom' name='sdc'/>"

LOADED_CD_METADATA_XML = """\
<ovirt-vm:vm xmlns:ovirt-vm="http://ovirt.org/vm/1.0">
    <ovirt-vm:device devtype="cdrom" name="sdc">
        <ovirt-vm:domainID>88252cf6-381e-48f0-8795-a294a32c7149</ovirt-vm:domainID>
        <ovirt-vm:imageID>89f05c7d-b961-4935-993f-514499024515</ovirt-vm:imageID>
        <ovirt-vm:poolID>13345997-b94f-42dd-b8ef-a1392f65cebf</ovirt-vm:poolID>
        <ovirt-vm:volumeID>626a493f-5214-4337-b580-96a1ce702c2a</ovirt-vm:volumeID>
    </ovirt-vm:device>
</ovirt-vm:vm>
"""  # NOQA: E501 (long line)

EMPTY_CD_DEVICE_XML = """\
<disk type='file' device='cdrom'>
  <driver name='qemu' error_policy='report'/>
  <source startupPolicy='optional'/>
  <target dev='sdc' bus='sata'/>
  <readonly/>
  <alias name='ua-79287c04-4eea-4db7-a376-99a9f85ad0ed'/>
  <address type='drive' controller='0' bus='0' target='0' unit='2'/>
</disk>
"""

LOADED_CD_DEVICE_XML = """\
<disk type='file' device='cdrom'>
  <driver name='qemu' type='raw' error_policy='report'/>
  <source dev='/path/to/image' index='2'>
    <seclabel model='dac' relabel='no'/>
  </source>
  <backingStore/>
  <target dev='sdc' bus='sata'/>
  <readonly/>
  <alias name='ua-79287c04-4eea-4db7-a376-99a9f85ad0ed'/>
  <address type='drive' controller='0' bus='0' target='0' unit='2'/>
</disk>
"""

CD_PDIV = {
    "poolID": "13345997-b94f-42dd-b8ef-a1392f65cebf",
    "domainID": "88252cf6-381e-48f0-8795-a294a32c7149",
    "imageID": "89f05c7d-b961-4935-993f-514499024515",
    "volumeID": "626a493f-5214-4337-b580-96a1ce702c2a",
}

LOADING_PDIV = {
    "poolID": "pool-id",
    "domainID": "domain-id",
    "imageID": "image-id",
    "volumeID": "volume-id",
}

LOADING_DRIVE_SPEC = dict(device="cdrom", **LOADING_PDIV)

LOADING_CHANGE = dict(state="loading", **LOADING_PDIV)

LOADING_METADATA = dict(change=LOADING_CHANGE, **CD_PDIV)


@pytest.fixture
def vm_with_cd():
    with fake.VM(
            cif=ClientIF(),
            devices=[{"type": "file", "device": "cdrom"}],
            create_device_objects=True,
            xmldevices=LOADED_CD_DEVICE_XML,
            metadata=LOADED_CD_METADATA_XML
    ) as fakevm:
        fakevm._dom = fake.Domain()

        # Prepare image for loaded CD.
        drive = dict(CD_PDIV)
        drive["device"] = "cdrom"
        fakevm.cif.prepareVolumePath(drive)

        yield fakevm


def test_change_cd_eject():
    with fake.VM(cif=ClientIF()) as fakevm:
        fakevm._dom = fake.Domain()
        cdromspec = {
            'path': '',
            'iface': 'ide',
            'index': '2',
        }
        fakevm.changeCD(cdromspec)


def test_change_cd_failure():
    with fake.VM(cif=ClientIF()) as fakevm:
        # no specific meaning, actually any error != None is good
        fakevm._dom = fake.Domain(virtError=libvirt.VIR_ERR_GET_FAILED)

        cdromspec = {
            'path': '/path/to/image',
            'iface': 'ide',
            'index': '2',
        }

        with pytest.raises(exception.ImageFileNotFound):
            fakevm.changeCD(cdromspec)


def test_change_loaded_cd(tmpdir, vm_with_cd):
    cd_path = str(tmpdir.join("fake_cd"))
    with open(cd_path, "w") as f:
        f.write("test")

    cdromspec = {
        "path": cd_path,
        "iface": "sata",
        "index": "2",
    }
    vm_with_cd.changeCD(cdromspec)

    expected_dev_xml = """\
<?xml version='1.0' encoding='utf-8'?>
<disk type="file" device="cdrom">
    <source file="{}" />
    <target dev="sdc" bus="sata" />
</disk>""".format(cd_path)
    assert normalized(expected_dev_xml) == normalized(vm_with_cd._dom.devXml)


def test_update_disk_device_failed():
    with fake.VM(cif=ClientIF()) as fakevm:
        fakevm._dom = fake.Domain(virtError=libvirt.VIR_ERR_XML_ERROR)

        with pytest.raises(exception.ChangeDiskFailed):
            fakevm._update_disk_device("<invalid-xml/>", force=False)


def test_change_cd_metadata_success(vm_with_cd):
    # Simulate metadata flow in change CD scenario. Expected flow is as
    # follows:
    # 1. Add PDIV for change CD into CDROM metadata.
    # 2. Prepare volume to be loaded.
    # 3. Change the CD using libvirt call.
    # 4. Tear down ejected volume.
    # 5. Update CDROM metadata and remove `change` element from the metadata.
    # This test tests only steps 1. and 5., storing and removing multiple
    # metadata items at the same time.
    block_dev = "sdc"

    # Start CD change - insert change CD metadata.
    vm_with_cd._add_cd_change(block_dev, LOADING_DRIVE_SPEC)

    with vm_with_cd._md_desc.device(devtype="cdrom", name=block_dev) as dev:
        assert dev == LOADING_METADATA

    # Finish CD change - remove change metadata and update CD PDIV.
    vm_with_cd._apply_cd_change(block_dev)
    with vm_with_cd._md_desc.device(devtype="cdrom", name=block_dev) as dev:
        assert dev == LOADING_PDIV


def test_change_cd_metadata_fail(vm_with_cd):
    # Simulate same scenarios as test_change_cd_metadata_success, but assumes
    # failure before we changed CD via libvirt (e.g. preparation of the volume
    # to be loaded failed).
    block_dev = "sdc"

    # Start CD change - insert change CD metadata.
    vm_with_cd._add_cd_change(block_dev, LOADING_DRIVE_SPEC)

    with vm_with_cd._md_desc.device(devtype="cdrom", name=block_dev) as dev:
        assert dev == LOADING_METADATA

    # Failure, discard cd change.
    vm_with_cd._discard_cd_change(block_dev)
    with vm_with_cd._md_desc.device(devtype="cdrom", name=block_dev) as dev:
        assert dev == CD_PDIV


def test_change_cd_loading():
    sd_id = str(uuid.uuid4())
    img_id = str(uuid.uuid4())
    vol_id = str(uuid.uuid4())
    drive_spec = {
        "device": "cdrom",
        "domainID": sd_id,
        "poolID": str(uuid.uuid4()),
        "imageID": img_id,
        "volumeID": vol_id,
    }
    cdrom_spec = {
        "iface": "sata",
        "index": "2",
        "drive_spec": drive_spec,
    }
    device = drivename.make(cdrom_spec["iface"], cdrom_spec["index"])

    with fake.VM(
            cif=ClientIF(),
            create_device_objects=True,
            xmldevices=EMPTY_CD_DEVICE_XML,
            metadata=EMPTY_CD_METADATA_XML
    ) as fakevm:
        fakevm._dom = fake.Domain()
        fakevm.changeCD(cdrom_spec)

        assert (sd_id, img_id, vol_id) in fakevm.cif.irs.prepared_volumes
        with fakevm._md_desc.device(devtype="cdrom", name=device) as dev:
            _assert_pdiv(drive_spec, dev)
            assert "change" not in dev


def test_change_cd_ejecting(vm_with_cd):
    sd_id = "88252cf6-381e-48f0-8795-a294a32c7149"
    vol_id = "626a493f-5214-4337-b580-96a1ce702c2a"

    # Eject CD.
    cdrom_spec = {
        "iface": "sata",
        "index": "2",
        "drive_spec": None,
    }
    device = drivename.make(cdrom_spec["iface"], cdrom_spec["index"])

    vm_with_cd.changeCD(cdrom_spec)

    assert (sd_id, vol_id) not in vm_with_cd.cif.irs.prepared_volumes
    with vm_with_cd._md_desc.device(devtype="cdrom", name=device) as dev:
        assert dev == {}


def test_change_cd(vm_with_cd):
    new_sd_id = str(uuid.uuid4())
    new_img_id = str(uuid.uuid4())
    new_vol_id = str(uuid.uuid4())
    new_drive_spec = {
        "device": "cdrom",
        "domainID": new_sd_id,
        "poolID": str(uuid.uuid4()),
        "imageID": new_img_id,
        "volumeID": new_vol_id,
    }
    cdrom_spec = {
        "iface": "sata",
        "index": "2",
        "drive_spec": new_drive_spec,
    }
    device = drivename.make(cdrom_spec["iface"], cdrom_spec["index"])

    # Change CD.
    vm_with_cd.changeCD(cdrom_spec)

    volume = (new_sd_id, new_img_id, new_vol_id)
    assert volume in vm_with_cd.cif.irs.prepared_volumes
    with vm_with_cd._md_desc.device(devtype="cdrom", name=device) as dev:
        _assert_pdiv(new_drive_spec, dev)
        assert "change" not in dev


def test_change_cd_failed_libvirt():
    sd_id = str(uuid.uuid4())
    vol_id = str(uuid.uuid4())
    drive_spec = {
        "device": "cdrom",
        "domainID": sd_id,
        "poolID": str(uuid.uuid4()),
        "imageID": str(uuid.uuid4()),
        "volumeID": vol_id,
    }
    cdrom_spec = {
        "iface": "sata",
        "index": "2",
        "drive_spec": drive_spec,
    }
    device = drivename.make(cdrom_spec["iface"], cdrom_spec["index"])

    with fake.VM(
            cif=ClientIF(),
            create_device_objects=True,
            xmldevices=EMPTY_CD_DEVICE_XML,
            metadata=EMPTY_CD_METADATA_XML
    ) as fakevm:
        fakevm._dom = fake.Domain(virtError=libvirt.VIR_ERR_XML_ERROR)

        # Verify, that ChangeDiskFailed is thrown when libvirt fails to update
        # disk device. No CD is loaded, so if libvirt succeeded, no other
        # exception is thrown.
        with pytest.raises(exception.ChangeDiskFailed):
            fakevm.changeCD(cdrom_spec)

        # We started with empty CD. Verify that the image was torn down and
        # metadata was reset back to empty.
        assert (sd_id, vol_id) not in fakevm.cif.irs.prepared_volumes
        with fakevm._md_desc.device(devtype="cdrom", name=device) as dev:
            assert dev == {}


def test_change_cd_failed_libvirt_and_vol_teardown(monkeypatch, vm_with_cd):
    device = drivename.make("sata", "2")

    vm_with_cd._dom = fake.Domain(virtError=libvirt.VIR_ERR_XML_ERROR)

    def failing_teardown(self, sdUUID, spUUID, imgUUID, volUUID=None):
        raise Exception("Image teardown failed.")

    monkeypatch.setattr(fake.IRS, "teardownImage", failing_teardown)

    # Verify, that ChangeDiskFailed is thrown when libvirt fails to update disk
    # device and teardown of loaded CD also fails. Contrary to
    # test_change_cd_failed_libvirt(), now CD is loaded so tear down of the CD
    # is called, which will fail as we monkey patch it.
    with pytest.raises(exception.ChangeDiskFailed):
        vm_with_cd._change_cd(device, LOADING_DRIVE_SPEC)


def test_change_cd_failed_libvirt_and_discard_cd_change(monkeypatch):
    device = drivename.make("sata", "2")

    def failing_discard_cd_change(self, device):
        raise Exception("Discard CD change failed")

    monkeypatch.setattr(vm.Vm, "_discard_cd_change", failing_discard_cd_change)

    with fake.VM(
            cif=ClientIF(),
            create_device_objects=True,
            xmldevices=EMPTY_CD_DEVICE_XML,
            metadata=EMPTY_CD_METADATA_XML
    ) as fakevm:
        fakevm._dom = fake.Domain(virtError=libvirt.VIR_ERR_XML_ERROR)

        # Verify, that ChangeDiskFailed is thrown when libvirt fails to update
        # disk device and _diskcard_cd_change() fails as well. Teardown will
        # pass and monkey patched _discard_cd_change() will raise.
        with pytest.raises(exception.ChangeDiskFailed):
            fakevm._change_cd(device, LOADING_DRIVE_SPEC)


def test_change_cd_teardown_old_cd_failed(monkeypatch, vm_with_cd):
    new_sd_id = str(uuid.uuid4())
    new_img_id = str(uuid.uuid4())
    new_vol_id = str(uuid.uuid4())
    new_drive_spec = {
        "device": "cdrom",
        "domainID": new_sd_id,
        "poolID": str(uuid.uuid4()),
        "imageID": new_img_id,
        "volumeID": new_vol_id,
    }
    new_cdrom_spec = {
        "iface": "sata",
        "index": "2",
        "drive_spec": new_drive_spec,
    }
    device = drivename.make(new_cdrom_spec["iface"], new_cdrom_spec["index"])

    def failing_teardown(self, sdUUID, spUUID, imgUUID, volUUID=None):
        raise Exception("Image teardown failed.")

    monkeypatch.setattr(fake.IRS, "teardownImage", failing_teardown)

    # Change CD. Tearing down old CD will fail. However, we ignore the failure,
    # as we already successfully updated VM disk, so no exception should be
    # thrown from this call.
    vm_with_cd.changeCD(new_cdrom_spec)

    # Check new CD is prepared.
    volume = (new_sd_id, new_img_id, new_vol_id)
    assert volume in vm_with_cd.cif.irs.prepared_volumes

    # Assert metadata is in consistent state and new CD is loaded.
    with vm_with_cd._md_desc.device(devtype="cdrom", name=device) as dev:
        _assert_pdiv(new_drive_spec, dev)
        assert "change" not in dev


def test_change_cd_apply_cd_change_failed(monkeypatch):
    old_sd_id = str(uuid.uuid4())
    old_vol_id = str(uuid.uuid4())
    old_drive_spec = {
        "device": "cdrom",
        "domainID": old_sd_id,
        "poolID": str(uuid.uuid4()),
        "imageID": str(uuid.uuid4()),
        "volumeID": old_vol_id,
    }
    old_cdrom_spec = {
        "iface": "sata",
        "index": "2",
        "drive_spec": old_drive_spec,
    }

    new_drive_spec = {
        "device": "cdrom",
        "domainID": str(uuid.uuid4()),
        "poolID": str(uuid.uuid4()),
        "imageID": str(uuid.uuid4()),
        "volumeID": str(uuid.uuid4()),
    }
    new_cdrom_spec = {
        "iface": "sata",
        "index": "2",
        "drive_spec": new_drive_spec,
    }
    device = drivename.make(new_cdrom_spec["iface"], new_cdrom_spec["index"])

    with fake.VM(
            cif=ClientIF(),
            create_device_objects=True,
            xmldevices=EMPTY_CD_DEVICE_XML,
            metadata=EMPTY_CD_METADATA_XML
    ) as fakevm:
        fakevm._dom = fake.Domain()

        # Insert CD.
        fakevm.changeCD(old_cdrom_spec)

        def failing_apply_cd_change(self, device):
            raise Exception("Apply CD change failed")

        monkeypatch.setattr(
            vm.Vm, "_apply_cd_change", failing_apply_cd_change)

        # Change CD. Apply cd change to metadata will fail, but as we already
        # succeeded to change CD, we ignore this error. The old disk should be
        # torn down and the call should succeed.
        fakevm.changeCD(new_cdrom_spec)

        # Tear down of the old image should succeed.
        assert (old_sd_id, old_vol_id) not in fakevm.cif.irs.prepared_volumes

        # As updating of metadata failed, metadata will be in inconsistent
        # state and should contain old CD and `change` element with new CD
        # PDIV.
        with fakevm._md_desc.device(devtype="cdrom", name=device) as dev:
            _assert_pdiv(old_drive_spec, dev)
            assert "change" in dev
            _assert_pdiv(new_drive_spec, dev["change"])


def _assert_pdiv(expected, actual):
    assert expected["poolID"] == actual["poolID"]
    assert expected["domainID"] == actual["domainID"]
    assert expected["imageID"] == actual["imageID"]
    assert expected["volumeID"] == actual["volumeID"]


class ClientIF(clientIF.clientIF):
    log = logging.getLogger('cd_test.ClientIF')

    def __init__(self):
        self.irs = fake.IRS()
        self.channelListener = None
        self.vmContainer = {}
