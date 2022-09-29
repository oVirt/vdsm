# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

import base64
import libvirt
import uuid

from monkeypatch import Patch
from testlib import VdsmTestCase, XMLTestCase
from testlib import expandPermutations, permutations
import vmfakecon

from vdsm.common import libvirtconnection
from vdsm.common import response
from vdsm.common.password import ProtectedPassword
from vdsm.virt import secret
import pytest


class Unexpected(Exception):
    """ Unexpected error """


@expandPermutations
class SecretTests(VdsmTestCase):

    @permutations((["uuid"], ["usageType"], ["usageID"], ["password"]))
    def test_missing_required_params(self, name):
        params = make_secret()
        del params[name]
        with pytest.raises(ValueError):
            secret.Secret(params)

    @permutations((["ceph"], ["volume"], ["iscsi"]))
    def test_supported_usage_types(self, usage_type):
        params = make_secret(usage_type=usage_type)
        s = secret.Secret(params)
        assert s.usage_type == usage_type

    def test_unsupported_usage_types(self):
        params = make_secret(usage_type="unsupported")
        with pytest.raises(ValueError):
            secret.Secret(params)

    def test_unencoded_password(self):
        params = make_secret()
        params["password"] = ProtectedPassword("not base64 value")
        with pytest.raises(ValueError):
            secret.Secret(params)

    def test_encoded_password(self):
        params = make_secret(password="12345678")
        s = secret.Secret(params)
        assert s.password.value == b"12345678"

    def test_register(self):
        params = make_secret(password="12345678")
        sec = secret.Secret(params)
        con = vmfakecon.Connection()
        sec.register(con)
        virsec = con.secrets[sec.uuid]
        assert virsec.value == b"12345678"


class SecretXMLTests(XMLTestCase):

    def test_type_ceph(self):
        xml = """
        <secret ephemeral="yes" private="yes">
            <uuid>3a27b133-abb2-4302-8891-bd0a4032866f</uuid>
            <usage type="ceph">
                <name>ovirt/3a27b133-abb2-4302-8891-bd0a4032866f</name>
            </usage>
        </secret>
        """
        params = make_secret(sid='3a27b133-abb2-4302-8891-bd0a4032866f',
                             usage_type="ceph")
        self.check(params, xml)

    def test_type_volume(self):
        xml = """
        <secret ephemeral="yes" private="yes">
            <uuid>3a27b133-abb2-4302-8891-bd0a4032866f</uuid>
            <usage type="volume">
                <volume>ovirt/3a27b133-abb2-4302-8891-bd0a4032866f</volume>
            </usage>
        </secret>
        """
        params = make_secret(sid='3a27b133-abb2-4302-8891-bd0a4032866f',
                             usage_type="volume")
        self.check(params, xml)

    def test_type_iscsi(self):
        xml = """
        <secret ephemeral="yes" private="yes">
            <uuid>3a27b133-abb2-4302-8891-bd0a4032866f</uuid>
            <usage type="iscsi">
                <target>ovirt/3a27b133-abb2-4302-8891-bd0a4032866f</target>
            </usage>
        </secret>
        """
        params = make_secret(sid='3a27b133-abb2-4302-8891-bd0a4032866f',
                             usage_type="iscsi")
        self.check(params, xml)

    def test_description(self):
        xml = """
        <secret ephemeral="yes" private="yes">
            <description>text</description>
            <uuid>3a27b133-abb2-4302-8891-bd0a4032866f</uuid>
            <usage type="ceph">
                <name>ovirt/3a27b133-abb2-4302-8891-bd0a4032866f</name>
            </usage>
        </secret>
        """
        params = make_secret(sid='3a27b133-abb2-4302-8891-bd0a4032866f',
                             description="text")
        self.check(params, xml)

    def test_escape(self):
        xml = """
        <secret ephemeral="yes" private="yes">
            <description>&lt; &amp; &gt;</description>
            <uuid>3a27b133-abb2-4302-8891-bd0a4032866f</uuid>
            <usage type="ceph">
                <name>&lt; &amp; &gt;</name>
            </usage>
        </secret>
        """
        params = make_secret(sid='3a27b133-abb2-4302-8891-bd0a4032866f',
                             description="< & >", usage_id="< & >")
        self.check(params, xml)

    def check(self, params, xml):
        s = secret.Secret(params)
        self.assertXMLEqual(s.toxml(), xml)


class APITests(VdsmTestCase):

    def setUp(self):
        self.connection = vmfakecon.Connection()
        self.patch = Patch([
            (libvirtconnection, 'get', lambda: self.connection)
        ])
        self.patch.apply()

    def tearDown(self):
        self.patch.revert()
        libvirtconnection._clear()

    def test_clear(self):
        self.connection.secrets = {
            "uuid1": vmfakecon.Secret(self.connection, "uuid1", "ceph",
                                      "ovirt/name1", None),
            "uuid2": vmfakecon.Secret(self.connection, "uuid2", "ceph",
                                      "name2", None),
        }
        secret.clear()
        assert "uuid1" not in self.connection.secrets
        assert "uuid2" in self.connection.secrets

    def test_clear_skip_failed(self):
        def fail():
            raise vmfakecon.Error(libvirt.VIR_ERR_INTERNAL_ERROR)
        self.connection.secrets = {
            "uuid1": vmfakecon.Secret(self.connection, "uuid1", "ceph",
                                      "ovirt/name1", None),
            "uuid2": vmfakecon.Secret(self.connection, "uuid2", "ceph",
                                      "ovirt/name2", None),
        }
        self.connection.secrets["uuid1"].undefine = fail
        secret.clear()
        assert "uuid2" not in self.connection.secrets

    def test_register_validation(self):
        res = secret.register([{"invalid": "secret"}])
        assert res == response.error("secretBadRequestErr")

    def test_register_new(self):
        sec1 = make_secret(password="sec1 password")
        sec2 = make_secret(password="sec2 password")
        res = secret.register([sec1, sec2])
        assert res == response.success()
        virsec1 = self.connection.secrets[sec1["uuid"]]
        assert b"sec1 password" == virsec1.value
        virsec2 = self.connection.secrets[sec2["uuid"]]
        assert b"sec2 password" == virsec2.value

    def test_register_replace(self):
        # Register 2 secrets
        sec1 = make_secret(password="sec1 password")
        sec2 = make_secret(password="sec2 password")
        secret.register([sec1, sec2])
        # Replace existing secret value
        sec2["password"] = make_password("sec2 new password")
        res = secret.register([sec2])
        assert res == response.success()
        virsec1 = self.connection.secrets[sec1["uuid"]]
        assert b"sec1 password" == virsec1.value
        virsec2 = self.connection.secrets[sec2["uuid"]]
        assert b"sec2 new password" == virsec2.value

    def test_register_change_usage_id(self):
        sec = make_secret(usage_id="ovirt/provider_uuid/secert_uuid")
        secret.register([sec])
        # Change usage id
        sec["usageID"] = "ovirt/domain_uuid/secret_uuid"
        res = secret.register([sec])
        assert res == response.success()
        virsec = self.connection.secrets[sec["uuid"]]
        assert "ovirt/domain_uuid/secret_uuid" == virsec.usage_id

    def test_register_clear(self):
        self.connection.secrets = {
            "uuid1": vmfakecon.Secret(self.connection, "uuid1", "ceph",
                                      "ovirt/name1", None),
            "uuid2": vmfakecon.Secret(self.connection, "uuid2", "ceph",
                                      "name2", None),
        }
        sec = make_secret()
        res = secret.register([sec], clear=True)
        # Should succeed
        assert res == response.success()
        # Should remove existing ovirt secrets
        assert "uuid1" not in self.connection.secrets
        # Should keep non-ovirt secrets
        assert "uuid2" in self.connection.secrets
        # Should register new secret
        virsec = self.connection.secrets[sec["uuid"]]
        assert sec["password"].value == virsec.value

    def test_register_libvirt_error(self):
        def fail(xml):
            raise vmfakecon.Error(libvirt.VIR_ERR_INTERNAL_ERROR)
        self.connection.secretDefineXML = fail
        res = secret.register([make_secret()])
        assert res == response.error("secretRegisterErr")

    def test_register_unexpected_error(self):
        def fail(xml):
            raise Unexpected
        self.connection.secretDefineXML = fail
        with pytest.raises(Unexpected):
            secret.register([make_secret()])

    def test_unregister_validation(self):
        res = secret.unregister(["this-is-not-a-uuid"])
        assert res == response.error("secretBadRequestErr")

    def test_unregister_existing(self):
        sec1 = make_secret(password="sec1 password")
        sec2 = make_secret(password="sec2 password")
        secret.register([sec1, sec2])
        res = secret.unregister([sec1["uuid"]])
        assert res == response.success()
        assert sec1["uuid"] not in self.connection.secrets
        assert sec2["uuid"] in self.connection.secrets

    def test_unregister_missing(self):
        existing_sec = make_secret()
        secret.register([existing_sec])
        missing_sec = make_secret()
        res = secret.unregister([missing_sec["uuid"], existing_sec["uuid"]])
        assert res == response.success()
        assert {} == self.connection.secrets

    def test_unregister_libvirt_error(self):
        def fail(uuid):
            raise vmfakecon.Error(libvirt.VIR_ERR_INTERNAL_ERROR)
        self.connection.secretLookupByUUIDString = fail
        res = secret.unregister([str(uuid.uuid4())])
        assert res == response.error("secretUnregisterErr")

    def test_unregister_unexpected_error(self):
        def fail(uuid):
            raise Unexpected
        self.connection.secretLookupByUUIDString = fail
        with pytest.raises(Unexpected):
            secret.unregister([str(uuid.uuid4())])


def make_secret(sid=None, usage_type="ceph", usage_id=None,
                password="12345678", description=None):
    if sid is None:
        sid = str(uuid.uuid4())

    if usage_id is None:
        usage_id = "ovirt/" + sid

    params = {
        "uuid": sid,
        "usageType": usage_type,
        "usageID": usage_id,
        "password": make_password(password),
    }

    if description:
        params["description"] = description

    return params


def make_password(value):
    return ProtectedPassword(base64.b64encode(value.encode('utf8')))
