# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

import uuid

import libvirt
import pytest

from vdsm.virt.vmdevices import storage

from . import vmfakelib


def test_secret_define_new():
    con = vmfakelib.Connection()
    xml = """
    <secret>
        <uuid>uuid</uuid>
        <usage type="ceph">
            <name>name</name>
        </usage>
    </secret>
    """
    con.secretDefineXML(xml)
    sec = con.secrets['uuid']
    assert sec.uuid == "uuid"
    assert sec.usage_type == "ceph"
    assert sec.usage_id == "name"
    assert sec.description is None


def test_secret_define_new_with_description():
    con = vmfakelib.Connection()
    xml = """
    <secret>
        <description>description</description>
        <uuid>uuid</uuid>
        <usage type="ceph">
            <name>name</name>
        </usage>
    </secret>
    """
    con.secretDefineXML(xml)
    sec = con.secrets['uuid']
    assert sec.description == "description"


def test_secret_define_replace():
    con = vmfakelib.Connection()
    xml1 = """
    <secret>
        <description>old description</description>
        <uuid>uuid</uuid>
        <usage type="ceph">
            <name>name</name>
        </usage>
    </secret>
    """
    xml2 = """
    <secret>
        <description>new description</description>
        <uuid>uuid</uuid>
        <usage type="ceph">
            <name>name</name>
        </usage>
    </secret>
    """
    con.secretDefineXML(xml1)
    con.secretDefineXML(xml2)
    sec = con.secrets['uuid']
    assert sec.description == "new description"


def test_secret_define_cannot_change_usage_id():
    con = vmfakelib.Connection()
    xml1 = """
    <secret>
        <uuid>uuid</uuid>
        <usage type="ceph">
            <name>name 1</name>
        </usage>
    </secret>
    """
    xml2 = """
    <secret>
        <uuid>uuid</uuid>
        <usage type="ceph">
            <name>name 2</name>
        </usage>
    </secret>
    """
    con.secretDefineXML(xml1)
    with pytest.raises(libvirt.libvirtError) as e:
        con.secretDefineXML(xml2)
    assert e.value.get_error_code() == libvirt.VIR_ERR_INTERNAL_ERROR


def test_secret_define_usage_not_unique():
    con = vmfakelib.Connection()
    xml1 = """
    <secret>
        <uuid>uuid 1</uuid>
        <usage type="ceph">
            <name>name</name>
        </usage>
    </secret>
    """
    xml2 = """
    <secret>
        <uuid>uuid 2</uuid>
        <usage type="ceph">
            <name>name</name>
        </usage>
    </secret>
    """
    con.secretDefineXML(xml1)
    with pytest.raises(libvirt.libvirtError) as e:
        con.secretDefineXML(xml2)
    assert e.value.get_error_code() == libvirt.VIR_ERR_INTERNAL_ERROR


def test_secret_lookup():
    con = vmfakelib.Connection()
    xml = """
    <secret>
        <uuid>uuid</uuid>
        <usage type="ceph">
            <name>name</name>
        </usage>
    </secret>
    """
    con.secretDefineXML(xml)
    sec = con.secretLookupByUUIDString('uuid')
    assert sec.usage_id == "name"


def test_secret_lookup_error():
    con = vmfakelib.Connection()
    with pytest.raises(libvirt.libvirtError) as e:
        con.secretLookupByUUIDString('no-such-uuid')
    assert e.value.get_error_code() == libvirt.VIR_ERR_NO_SECRET


def test_irs_prepared_volumes():
    sdUUID = uuid.uuid4()
    spUUID = uuid.uuid4()
    imgUUID = uuid.uuid4()
    leafUUID = uuid.uuid4()
    irs = vmfakelib.IRS()
    expected_path = "/run/storage/{}/{}/{}".format(sdUUID, imgUUID, leafUUID)

    res = irs.prepareImage(sdUUID, spUUID, imgUUID, leafUUID)
    assert (sdUUID, imgUUID, leafUUID) in irs.prepared_volumes
    assert res == {
        "status": {
            "code": 0,
            "message": "Done"
        },
        "path": expected_path,
        "info": {
            "type": storage.DISK_TYPE.FILE,
            "path": expected_path,
        },
        "imgVolumesInfo": None,
    }

    res = irs.teardownImage(sdUUID, spUUID, imgUUID, leafUUID)
    assert (sdUUID, imgUUID, leafUUID) not in irs.prepared_volumes
    assert res == {
        "status": {
            "code": 0,
            "message": "Done"
        },
    }
