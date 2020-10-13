# encoding: utf-8
#
# Copyright 2018 Red Hat, Inc.
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
from __future__ import absolute_import
from __future__ import division

import libvirt
import pytest

from vdsm.virt import virdomain
from vdsm.virt import xmlconstants

from testlib import VdsmTestCase

from . import vmfakelib as fake


class TestDisconnected(VdsmTestCase):

    def setUp(self):
        self.vmid = 'test-vm-id'
        self.dom = virdomain.Disconnected(self.vmid)

    def test_connected(self):
        self.assertFalse(self.dom.connected)

    def test_getattr(self):
        with self.assertRaises(virdomain.NotConnectedError):
            # any method invocation is fine
            self.dom.state(0)


class TestDefined(VdsmTestCase):

    def setUp(self):
        self.vmid = 'test-vm-id'
        self.libvirtid = 'test-libvirt-id'
        self.libvirtdom = fake.Domain(vmId=self.libvirtid)
        self.dom = virdomain.Defined(self.vmid, self.libvirtdom)

    def test_connected(self):
        self.assertFalse(self.dom.connected)

    def test_getattr(self):
        with self.assertRaises(virdomain.NotConnectedError):
            # we need to call a method not explicitely declared,
            # to exercise getattr
            self.dom.XMLDesc(0)

    def test_state(self):
        self.assertEqual(
            self.dom.state(0),
            (libvirt.VIR_DOMAIN_RUNNING, 0)
        )

    def test_UUIDString(self):
        self.assertEqual(
            self.dom.UUIDString(),
            self.libvirtid
        )

    def test_metadata(self):
        md_xml = '<metadata>random test garbage</metadata>'
        self.dom.setMetadata(
            libvirt.VIR_DOMAIN_METADATA_ELEMENT,
            md_xml,
            xmlconstants.METADATA_VM_VDSM_PREFIX,
            xmlconstants.METADATA_VM_VDSM_URI,
            0
        )
        self.assertEqual(
            self.dom.metadata(
                libvirt.VIR_DOMAIN_METADATA_ELEMENT,
                xmlconstants.METADATA_VM_VDSM_URI,
                0
            ),
            md_xml
        )

    def undefineFlags(self, flags=0):
        self.assertNotRaises(
            self.dom.undefineFlags,
            0
        )


class TestNotifying(VdsmTestCase):

    def setUp(self):
        self.vmid = 'test-vm-id'
        self.libvirtid = 'test-libvirt-id'
        self.libvirtdom = fake.Domain(vmId=self.libvirtid)
        self.dom = virdomain.Notifying(self.libvirtdom, self.tocb)
        self.elapsed = None

    def tocb(self, elapsed):
        self.elapsed = elapsed

    def test_connected(self):
        self.assertTrue(self.dom.connected)

    def test_call(self):
        self.assertEqual(
            self.dom.state(0),
            (libvirt.VIR_DOMAIN_RUNNING, 0)
        )
        self.assertIsNot(self.elapsed, None)
        self.assertFalse(self.elapsed)

    def test_call_timeout(self):
        def _fail(*args, **kwargs):
            e = libvirt.libvirtError("timeout")
            e.err = (libvirt.VIR_ERR_OPERATION_TIMEOUT, '', 'timeout')
            raise e

        self.libvirtdom.state = _fail
        with self.assertRaises(virdomain.TimeoutError):
            self.dom.state(0)
        self.assertIsNot(self.elapsed, None)
        self.assertTrue(self.elapsed)

    def test_call_error(self):
        def _fail(*args, **kwargs):
            e = libvirt.libvirtError("timeout")
            e.err = (libvirt.VIR_ERR_NO_DOMAIN_METADATA, '', 'random error')
            raise e

        self.libvirtdom.state = _fail
        with self.assertRaises(libvirt.libvirtError):
            # any method is fine
            self.dom.state(0)
        self.assertIs(self.elapsed, None)


class TestExpose:

    def test_expose(self):
        dom = FakeDom()
        f = VMFreezer(FakeVM(dom))
        f.fsFreeze(["sda"])
        f.fsThaw(["sda"])

        assert dom.calls == [
            ("fsFreeze", ["sda"], 0),
            ("fsThaw", ["sda"], 0),
        ]

    def test_wrapping(self):
        dom = FakeDom()
        f = VMFreezer(FakeVM(dom))
        orig = libvirt.virDomain.fsFreeze
        for name in "__doc__", "__name__":
            assert getattr(f.fsFreeze, name) == getattr(orig, name)

    def test_replace_dom(self):
        dom = FakeDom()
        vm = FakeVM(dom)
        f = VMFreezer(vm)

        # Simulate disconnection...
        vm._dom = virdomain.Disconnected("dom-id")

        with pytest.raises(virdomain.NotConnectedError):
            f.fsFreeze()


@virdomain.expose("fsFreeze", "fsThaw")
class VMFreezer(object):

    def __init__(self, vm):
        self._vm = vm


class FakeVM(object):

    def __init__(self, dom):
        self._dom = dom


class FakeDom(object):

    def __init__(self):
        self.calls = []

    def fsFreeze(self, mountpoints=None, flags=0):
        self.calls.append(('fsFreeze', mountpoints, flags))

    def fsThaw(self, mountpoints=None, flags=0):
        self.calls.append(('fsThaw', mountpoints, flags))
