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

from testlib import normalized

from . import vmfakelib as fake

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


def test_change_cd_pdiv():
    sd_id = uuid.uuid4()
    img_id = uuid.uuid4()
    vol_id = uuid.uuid4()
    drivespec = {
        "device": "cdrom",
        "domainID": sd_id,
        "poolID": uuid.uuid4(),
        "imageID": img_id,
        "volumeID": vol_id,
    }

    with fake.VM(cif=ClientIF()) as fakevm:
        fakevm._dom = fake.Domain()
        cdromspec = {
            'path': drivespec,
            'iface': 'ide',
            'index': '2',
        }
        fakevm.changeCD(cdromspec)
        assert (sd_id, img_id, vol_id) in fakevm.cif.irs.prepared_volumes


def test_update_disk_device_failed():
    with fake.VM(cif=ClientIF()) as fakevm:
        fakevm._dom = fake.Domain(virtError=libvirt.VIR_ERR_XML_ERROR)

        with pytest.raises(exception.ChangeDiskFailed):
            fakevm._update_disk_device("<invalid-xml/>", force=False)


class ClientIF(clientIF.clientIF):
    log = logging.getLogger('cd_test.ClientIF')

    def __init__(self):
        self.irs = fake.IRS()
        self.channelListener = None
        self.vmContainer = {}
