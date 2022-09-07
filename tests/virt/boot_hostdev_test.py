# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

import io
import os
import sys


from testlib import XMLTestCase
from testlib import expandPermutations
from testlib import permutations
from testlib import temporaryPath

from vdsm.common import commands


_HOOK_PATH = '../vdsm_hooks/boot_hostdev/before_vm_start.py'
_DEV_NAMES = [
    ['pci_0000_0b_00_0'],
    ['scsi_2_0_0_0'],
    ['usb_usb7'],
    ['usb_2_5'],
]
_UNRELATED_DEV_NAMES = [
    ['pci_0000_0c_00_5'],
    ['scsi_3_0_0_1'],
    ['usb_usb5'],
    ['usb_3_6'],
]

_HOSTDEV_XML = """<?xml version="1.0" encoding="utf-8"?>
  <domain type="kvm" xmlns:ovirt="http://ovirt.org/vm/tune/1.0">
    <name>test</name>
    <devices>
      <disk type='file' device='disk' snapshot='no'>
        <boot order='1'/>
      </disk>
      <disk type='file' device='cdrom'>
        <boot order='2'/>
      </disk>
      <interface type='bridge'>
        <boot order='3'/>
      </interface>
      <hostdev mode='subsystem' type='pci' managed='no'>
        <driver name='vfio'/>
        <source>
          <address domain='0x0000' bus='0x0b' slot='0x00' function='0x0'/>
        </source>
      </hostdev>
      <hostdev mode='subsystem' type='scsi' managed='no' rawio='yes'>
        <source>
          <adapter name='scsi_host2'/>
          <address bus='0' target='0' unit='0'/>
        </source>
      </hostdev>
      <hostdev managed="no" mode="subsystem" type="usb">
          <source>
              <address bus="7" device="1"/>
          </source>
      </hostdev>
      <hostdev managed="no" mode="subsystem" type="usb">
          <source>
              <address bus="2" device="5"/>
          </source>
      </hostdev>
    </devices>
  </domain>"""

_EXPECTED_HOSTDEV_XML = {
    'pci_0000_0b_00_0': """<?xml version="1.0" encoding="utf-8"?>
      <domain type="kvm" xmlns:ovirt="http://ovirt.org/vm/tune/1.0">
        <name>test</name>
        <devices>
          <disk type='file' device='disk' snapshot='no'>
            <boot order='2'/>
          </disk>
          <disk type='file' device='cdrom'>
            <boot order='3'/>
          </disk>
          <interface type='bridge'>
            <boot order='4'/>
          </interface>
          <hostdev mode='subsystem' type='pci' managed='no'>
            <driver name='vfio'/>
            <source>
              <address domain='0x0000' bus='0x0b' slot='0x00' function='0x0'/>
            </source>
            <boot order='1'/>
          </hostdev>
          <hostdev mode='subsystem' type='scsi' managed='no' rawio='yes'>
            <source>
              <adapter name='scsi_host2'/>
              <address bus='0' target='0' unit='0'/>
            </source>
          </hostdev>
          <hostdev managed="no" mode="subsystem" type="usb">
            <source>
              <address bus="7" device="1"/>
            </source>
          </hostdev>
          <hostdev managed="no" mode="subsystem" type="usb">
            <source>
              <address bus="2" device="5"/>
            </source>
          </hostdev>
        </devices>
      </domain>""",
    'scsi_2_0_0_0': """<?xml version="1.0" encoding="utf-8"?>
      <domain type="kvm" xmlns:ovirt="http://ovirt.org/vm/tune/1.0">
        <name>test</name>
        <devices>
          <disk type='file' device='disk' snapshot='no'>
            <boot order='2'/>
          </disk>
          <disk type='file' device='cdrom'>
           <boot order='3'/>
          </disk>
          <interface type='bridge'>
            <boot order='4'/>
          </interface>
          <hostdev mode='subsystem' type='pci' managed='no'>
            <driver name='vfio'/>
            <source>
              <address domain='0x0000' bus='0x0b' slot='0x00' function='0x0'/>
            </source>
          </hostdev>
          <hostdev mode='subsystem' type='scsi' managed='no' rawio='yes'>
            <source>
              <adapter name='scsi_host2'/>
              <address bus='0' target='0' unit='0'/>
            </source>
            <boot order='1'/>
          </hostdev>
          <hostdev managed="no" mode="subsystem" type="usb">
            <source>
              <address bus="7" device="1"/>
            </source>
          </hostdev>
          <hostdev managed="no" mode="subsystem" type="usb">
            <source>
              <address bus="2" device="5"/>
            </source>
          </hostdev>
        </devices>
      </domain>""",
    'usb_usb7': """<?xml version="1.0" encoding="utf-8"?>
      <domain type="kvm" xmlns:ovirt="http://ovirt.org/vm/tune/1.0">
        <name>test</name>
        <devices>
          <disk type='file' device='disk' snapshot='no'>
            <boot order='2'/>
          </disk>
          <disk type='file' device='cdrom'>
            <boot order='3'/>
          </disk>
          <interface type='bridge'>
            <boot order='4'/>
          </interface>
          <hostdev mode='subsystem' type='pci' managed='no'>
            <driver name='vfio'/>
            <source>
              <address domain='0x0000' bus='0x0b' slot='0x00' function='0x0'/>
            </source>
          </hostdev>
          <hostdev mode='subsystem' type='scsi' managed='no' rawio='yes'>
            <source>
              <adapter name='scsi_host2'/>
              <address bus='0' target='0' unit='0'/>
            </source>
          </hostdev>
          <hostdev managed="no" mode="subsystem" type="usb">
            <source>
              <address bus="7" device="1"/>
            </source>
            <boot order='1'/>
          </hostdev>
          <hostdev managed="no" mode="subsystem" type="usb">
            <source>
              <address bus="2" device="5"/>
            </source>
          </hostdev>
        </devices>
      </domain>""",
    'usb_2_5': """<?xml version="1.0" encoding="utf-8"?>
      <domain type="kvm" xmlns:ovirt="http://ovirt.org/vm/tune/1.0">
        <name>test</name>
        <devices>
          <disk type='file' device='disk' snapshot='no'>
            <boot order='2'/>
          </disk>
          <disk type='file' device='cdrom'>
            <boot order='3'/>
          </disk>
          <interface type='bridge'>
            <boot order='4'/>
          </interface>
          <hostdev mode='subsystem' type='pci' managed='no'>
            <driver name='vfio'/>
            <source>
              <address domain='0x0000' bus='0x0b' slot='0x00' function='0x0'/>
            </source>
          </hostdev>
          <hostdev mode='subsystem' type='scsi' managed='no' rawio='yes'>
            <source>
              <adapter name='scsi_host2'/>
              <address bus='0' target='0' unit='0'/>
            </source>
          </hostdev>
          <hostdev managed="no" mode="subsystem" type="usb">
            <source>
              <address bus="7" device="1"/>
            </source>
          </hostdev>
          <hostdev managed="no" mode="subsystem" type="usb">
            <source>
              <address bus="2" device="5"/>
            </source>
            <boot order='1'/>
          </hostdev>
        </devices>
      </domain>"""
}


@expandPermutations
class BootHostdevHookTests(XMLTestCase):

    @permutations(_UNRELATED_DEV_NAMES)
    def test_ignore_unrelated_hostdev(self, dev_name):
        xml, rc, out, err = self._run_hook(_HOSTDEV_XML, boot_hostdev=dev_name)
        self.assertXMLEqual(xml, _HOSTDEV_XML)
        assert rc == 1

    @permutations(_DEV_NAMES)
    def test_boot_order_hostdev(self, dev_name):
        xml, rc, out, err = self._run_hook(_HOSTDEV_XML, boot_hostdev=dev_name)
        self.assertXMLEqual(xml, _EXPECTED_HOSTDEV_XML[dev_name])
        assert rc == 0

    def _setup_env(self, temp_path, boot_hostdev):
        env = dict(os.environ)
        env["PYTHONPATH"] = ":".join([os.environ["PYTHONPATH"], _HOOK_PATH])
        env["_hook_domxml"] = temp_path
        env["boot_hostdev"] = boot_hostdev
        return env

    def _run_hook(self, domxml, boot_hostdev):
        with temporaryPath(data=domxml.encode('utf-8')) as temp_path:
            env = self._setup_env(temp_path, boot_hostdev)
            rc, out, err = commands.execCmd(
                [sys.executable, _HOOK_PATH], env=env)
            with io.open(temp_path, 'r') as outxml:
                outxml = outxml.read()
            return outxml, rc, out, err
