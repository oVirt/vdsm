#
# Copyright 2017 Red Hat, Inc.
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

from __future__ import absolute_import

from contextlib import contextmanager
import os
import sys

from testlib import XMLTestCase
from testlib import expandPermutations
from testlib import permutations
from testlib import temporaryPath

from monkeypatch import MonkeyPatch

# placeholder for import
hostdev_scsi_hook = None


_HOOK_PATH = '../vdsm_hooks/hostdev_scsi'
_DEV_TYPES = [
    ['scsi_hd'],
    ['scsi_block'],
]

_HOSTDEV_XML = """<?xml version="1.0" encoding="utf-8"?>
  <domain type="kvm" xmlns:ovirt="http://ovirt.org/vm/tune/1.0">
    <name>test</name>
    <devices>
      <hostdev mode='subsystem' type='scsi' managed='no' rawio='yes'>
        <source>
          <adapter name='scsi_host1'/>
          <address bus='0' target='6' unit='0'/>
        </source>
        <alias name='hostdev0'/>
        <address type='drive' controller='0' bus='1' target='0' unit='1'/>
      </hostdev>
    </devices>
  </domain>"""


_MINIMAL_HOSTDEV_XML = """<?xml version="1.0" encoding="utf-8"?>
  <domain type="kvm" xmlns:ovirt="http://ovirt.org/vm/tune/1.0">
    <name>test</name>
    <devices>
      <hostdev mode='subsystem' type='scsi' managed='no' rawio='yes'>
        <source>
          <adapter name='scsi_host1'/>
          <address bus='0' target='6' unit='0'/>
        </source>
      </hostdev>
      <hostdev mode='subsystem' type='scsi' managed='no' rawio='yes'>
        <source>
          <adapter name='scsi_host1'/>
          <address bus='0' target='6' unit='1'/>
        </source>
      </hostdev>
    </devices>
  </domain>"""


@expandPermutations
class HostdevScsiHookTests(XMLTestCase):

    @classmethod
    def setUpClass(cls):
        # TODO: remove when we have proper vdsm_hooks package
        global hostdev_scsi_hook
        import sys
        sys.path.insert(0, _HOOK_PATH)
        import before_vm_start as hostdev_scsi_hook

    @classmethod
    def tearDownClass(cls):
        # TODO: remove when we have proper vdsm_hooks package
        sys.path.remove(_HOOK_PATH)

    def setUp(self):
        self.out_xml = None

    def test_ignore_not_configured(self):
        fake_xml = "hook should not read or write this"
        with self._hook_env(fake_xml):
            hostdev_scsi_hook.main()
        self.assertEqual(self.out_xml, fake_xml)

    def test_unsupported_type(self):
        with self._hook_env('<domain/>', hostdev_scsi='unsupported'):
            self.assertRaises(
                RuntimeError,
                hostdev_scsi_hook.main
            )

    @permutations(_DEV_TYPES)
    def test_ignore_not_hostdev(self, dev_type):
        expected_xml = """<?xml version="1.0" encoding="utf-8"?>
  <domain type="kvm" xmlns:ovirt="http://ovirt.org/vm/tune/1.0">
    <name>test</name>
    <devices>
      <emulator>kvm</emulator>
      <input bus="ps2" type="mouse"/>
      <memballoon model="none"/>
      <video>
        <model heads="1" ram="65536" type="qxl" vgamem="16384" vram="32768"/>
      </video>
    </devices>
  </domain>"""
        with self._hook_env(expected_xml, hostdev_scsi=dev_type):
            hostdev_scsi_hook.main()
        self.assertXMLEqual(self.out_xml, expected_xml)

    @permutations(_DEV_TYPES)
    def test_ignore_unrelared_hostdev(self, dev_type):
        expected_xml = """<?xml version="1.0" encoding="utf-8"?>
  <domain type="kvm" xmlns:ovirt="http://ovirt.org/vm/tune/1.0">
    <name>test</name>
    <devices>
      <hostdev mode='subsystem' type='pci' managed='no'>
        <source>
          <address domain='0x0000' bus='0x00' slot='0x19' function='0x0'/>
        </source>
        <boot order='1'/>
      </hostdev>
      <hostdev managed="no" mode="subsystem" type="usb">
        <source>
          <address bus="1" device="1"/>
        </source>
      </hostdev>
      <hostdev mode='subsystem' type='scsi' managed='no'>
        <source>
          <adapter name='scsi_host0'/>
          <address bus='0' target='6' unit='0'/>
        </source>
        <alias name='hostdev0'/>
        <address type='drive' controller='0' bus='0' target='0' unit='1'/>
      </hostdev>
    </devices>
  </domain>"""
        with self._hook_env(expected_xml, hostdev_scsi=dev_type):
            hostdev_scsi_hook.main()
        self.assertXMLEqual(self.out_xml, expected_xml)

    @MonkeyPatch(os, 'listdir', lambda path: ['sdd'])
    def test_translate_hostdev_to_block(self):
        expected_xml = """<?xml version="1.0" encoding="utf-8"?>
  <domain type="kvm" xmlns:ovirt="http://ovirt.org/vm/tune/1.0">
    <name>test</name>
    <devices>
      <disk type='block' device='lun' rawio='yes'>
        <address type='drive' controller='0' bus='1' target='0' unit='1'/>
        <alias name='hostdev0'/>
        <driver name='qemu' type='raw' cache='none' io='native'/>
        <source dev='/dev/sdd'/>
        <target dev='sdaaa' bus='scsi'/>
      </disk>
    </devices>
  </domain>"""
        with self._hook_env(_HOSTDEV_XML, hostdev_scsi='scsi_block'):
            hostdev_scsi_hook.main()
        self.assertXMLEqual(self.out_xml, expected_xml)

    @MonkeyPatch(os, 'listdir', lambda path: ['sde'])
    def test_translate_hostdev_to_hd(self):
        expected_xml = """<?xml version="1.0" encoding="utf-8"?>
  <domain type="kvm" xmlns:ovirt="http://ovirt.org/vm/tune/1.0">
    <name>test</name>
    <devices>
      <disk type='block' device='disk'>
        <address type='drive' controller='0' bus='1' target='0' unit='1'/>
        <alias name='hostdev0'/>
        <driver name='qemu' type='raw' cache='none' io='native'/>
        <source dev='/dev/sde'/>
        <target dev='sdaaa' bus='scsi'/>
      </disk>
    </devices>
  </domain>"""
        with self._hook_env(_HOSTDEV_XML, hostdev_scsi='scsi_hd'):
            hostdev_scsi_hook.main()
        self.assertXMLEqual(self.out_xml, expected_xml)

    @MonkeyPatch(os, 'listdir', lambda path: ['fake'])
    def test_translate_minimal_hostdev_to_block(self):
        expected_xml = """<?xml version="1.0" encoding="utf-8"?>
  <domain type="kvm" xmlns:ovirt="http://ovirt.org/vm/tune/1.0">
    <name>test</name>
    <devices>
      <disk type='block' device='lun' rawio='yes'>
        <driver name='qemu' type='raw' cache='none' io='native'/>
        <source dev='/dev/fake'/>
        <target dev='sdaaa' bus='scsi'/>
      </disk>
      <disk type='block' device='lun' rawio='yes'>
        <driver name='qemu' type='raw' cache='none' io='native'/>
        <source dev='/dev/fake'/>
        <target dev='sdaab' bus='scsi'/>
      </disk>
    </devices>
  </domain>"""
        with self._hook_env(_MINIMAL_HOSTDEV_XML, hostdev_scsi='scsi_block'):
            hostdev_scsi_hook.main()
        self.assertXMLEqual(self.out_xml, expected_xml)

    @MonkeyPatch(os, 'listdir', lambda path: ['fake'])
    def test_translate_minimal_hostdev_to_hd(self):
        expected_xml = """<?xml version="1.0" encoding="utf-8"?>
  <domain type="kvm" xmlns:ovirt="http://ovirt.org/vm/tune/1.0">
    <name>test</name>
    <devices>
      <disk type='block' device='disk'>
        <driver name='qemu' type='raw' cache='none' io='native'/>
        <source dev='/dev/fake'/>
        <target dev='sdaaa' bus='scsi'/>
      </disk>
      <disk type='block' device='disk'>
        <driver name='qemu' type='raw' cache='none' io='native'/>
        <source dev='/dev/fake'/>
        <target dev='sdaab' bus='scsi'/>
      </disk>
    </devices>
  </domain>"""
        with self._hook_env(_MINIMAL_HOSTDEV_XML, hostdev_scsi='scsi_hd'):
            hostdev_scsi_hook.main()
        self.assertXMLEqual(self.out_xml, expected_xml)

    @contextmanager
    def _hook_env(self, domain_xml, **env_vars):
        with _setup_os_environ_vars(env_vars):
            with temporaryPath(data=domain_xml.encode('utf-8')) as path:
                os.environ['_hook_domxml'] = path
                try:
                    yield
                finally:
                    os.environ.pop('_hook_domxml')
                    with open(path) as res:
                        self.out_xml = res.read()


@contextmanager
def _setup_os_environ_vars(env_vars):
    saved = {}
    for key, value in env_vars.items():
        if key in os.environ:
            saved[key] = os.environ.pop(key)
        os.environ[key] = value
    try:
        yield
    finally:
        for key, value in env_vars.items():
            if key in saved:
                os.environ[key] = saved[key]
            else:
                del os.environ[key]
