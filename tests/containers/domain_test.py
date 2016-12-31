#
# Copyright 2015-2016 Red Hat, Inc.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License as published
# by the Free Software Foundation; either version 2 of the License, or
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

import uuid

import libvirt

from vdsm.virt.containers import docker
from vdsm.virt.containers import domain
from vdsm.virt.containers import doms
from vdsm.virt.containers import xmlfile

from monkeypatch import MonkeyPatchScope

from . import conttestlib


class DomainIdsTests(conttestlib.RunnableTestCase):

    def setUp(self):
        super(DomainIdsTests, self).setUp()
        self.xmldesc = """<?xml version="1.0" encoding="utf-8"?>
        <domain type="kvm" xmlns:ovirt="http://ovirt.org/vm/tune/1.0">
            <name>testVm</name>
            <uuid>%s</uuid>
            <maxMemory>0</maxMemory>
            <metadata>
              <ovirt:container
                xmlns:ovirt="http://ovirt.org/vm/containers/1.0">
              docker</ovirt:container>
              <ovirt:qos/>
            </metadata>
            <devices>
                <emulator>qemu-system-x86_64</emulator>
            </devices>
        </domain>
        """
        self.dom = domain.Domain(self.xmldesc % str(self.guid))

    def test_ID(self):
        self.assertEqual(self.dom.ID(), self.guid.int)

    def test_UUIDString(self):
        self.assertEqual(self.dom.UUIDString(), str(self.guid))


class DomainXMLTests(conttestlib.RunnableTestCase):

    def test_XMLDesc(self):
        dom_xml = conttestlib.minimal_dom_xml()
        dom = domain.Domain(dom_xml)
        self.assertEqual(dom.XMLDesc(0), dom_xml)

    def test_XMLDesc_ignore_flags(self):
        # TODO: provide XML to exercise all the features.
        _TEST_DOM_XML = conttestlib.minimal_dom_xml()
        dom = domain.Domain(_TEST_DOM_XML)
        self.assertEqual(
            dom.XMLDesc(libvirt.VIR_DOMAIN_XML_SECURE),
            _TEST_DOM_XML)
        self.assertEqual(
            dom.XMLDesc(libvirt.VIR_DOMAIN_XML_INACTIVE),
            _TEST_DOM_XML)
        self.assertEqual(
            dom.XMLDesc(libvirt.VIR_DOMAIN_XML_UPDATE_CPU),
            _TEST_DOM_XML)

    def test_missing_emulator_metadata(self):
        xmldesc = """<?xml version="1.0" encoding="utf-8"?>
        <domain type="kvm" xmlns:ovirt="http://ovirt.org/vm/tune/1.0">
            <name>testVm</name>
            <uuid>%s</uuid>
            <maxMemory>0</maxMemory>
            <metadata>
              <ovirt:qos/>
            </metadata>
            <devices>
                <emulator>qemu-system-x86_64</emulator>
            </devices>
        </domain>
        """ % str(uuid.uuid4())
        self.assertRaises(xmlfile.ConfigError,
                          domain.Domain,
                          xmldesc)


class DomainAPITests(conttestlib.RunnableTestCase):

    def test_reset(self):
        with conttestlib.fake_runtime_domain() as dom:
            dom.reset(0)

        # FIXME
        self.assertTrue(dom._rt.actions['start'], 2)
        self.assertTrue(dom._rt.actions['stop'], 1)

    def test_controlInfo(self):
        with conttestlib.fake_runtime_domain() as dom:
            info = dom.controlInfo()
        self.assertEqual(len(info), 3)
        # TODO: more testing

    def test_vcpus(self):
        with conttestlib.fake_runtime_domain() as dom:
            # TODO: meaningful test
            self.assertNotRaises(dom.vcpus)

    def test_info(self):
        with conttestlib.fake_runtime_domain() as dom:
            info = dom.info()
        self.assertEqual(info[0],
                         libvirt.VIR_DOMAIN_RUNNING)


class UnsupportedAPITests(conttestlib.RunnableTestCase):

    def test_migrate(self):
        dom = domain.Domain(conttestlib.minimal_dom_xml())
        self.assertRaises(libvirt.libvirtError,
                          dom.migrate,
                          {})


class RegistrationTests(conttestlib.RunnableTestCase):

    def test_destroy_registered(self):
        with conttestlib.tmp_run_dir():
            dom = domain.Domain.create(
                conttestlib.minimal_dom_xml()
            )

        existing_doms = doms.get_all()
        self.assertEqual(len(existing_doms), 1)
        self.assertEqual(dom.ID, existing_doms[0].ID)
        dom.destroy()
        self.assertEqual(doms.get_all(), [])

    def test_destroy_unregistered(self):
        # you need to call create() to register into `doms'.
        with conttestlib.tmp_run_dir():
            dom = domain.Domain(
                conttestlib.minimal_dom_xml()
            )

        self.assertEqual(doms.get_all(), [])
        self.assertRaises(libvirt.libvirtError, dom.destroy)

    def test_destroy_unregistered_forcefully(self):
        with conttestlib.tmp_run_dir():
            dom = domain.Domain.create(
                conttestlib.minimal_dom_xml()
            )

        doms.remove(dom.UUIDString())
        self.assertRaises(libvirt.libvirtError, dom.destroy)


class RecoveryTests(conttestlib.TestCase):

    def setUp(self):
        conttestlib.clear_doms()
        self.runtime = None

    def test_recover(self):
        vm_uuid = str(uuid.uuid4())

        def _wrap(rt_uuid=None):
            self.runtime = ResyncingRuntime(rt_uuid)
            return self.runtime

        with conttestlib.tmp_run_dir():
            with MonkeyPatchScope([
                (docker, 'Runtime', _wrap)
            ]):
                domain.Domain.recover(
                    vm_uuid,
                    conttestlib.minimal_dom_xml(vm_uuid),
                )

        existing_doms = doms.get_all()
        self.assertEqual(len(existing_doms), 1)
        self.assertEqual(existing_doms[0].UUIDString(), vm_uuid)
        self.assertTrue(self.runtime.recovered)


class ResyncingRuntime(object):

    def __init__(self, rt_uuid=None):
        self.uuid = '00000000-0000-0000-0000-000000000000'
        self.recovered = False

    @classmethod
    def available(cls):
        return True

    def recover(self):
        self.recovered = True

    def unit_name(self):
        raise AssertionError("should not be called")

    def configure(self, xml_tree):
        raise AssertionError("should not be called")

    def start(self, target=None):
        raise AssertionError("should not be called")

    def stop(self):
        raise AssertionError("should not be called")

    def status(self):
        raise AssertionError("should not be called")

    def runtime_name(self):
        raise AssertionError("should not be called")

    def setup(self):
        raise AssertionError("should not be called")

    def teardown(self):
        raise AssertionError("should not be called")

    @property
    def runtime_config(self):
        raise AssertionError("should not be called")
