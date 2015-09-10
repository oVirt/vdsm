# Copyright 2014 Red Hat, Inc.
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

from StringIO import StringIO


import v2v
from vdsm import libvirtconnection
from vdsm.password import ProtectedPassword


from nose.plugins.skip import SkipTest
from testlib import VdsmTestCase as TestCaseBase
from monkeypatch import MonkeyPatch


class VmMock(object):
    def name(self):
        return 'windows'

    def state(self, flags=0):
        return [5, 0]

    def XMLDesc(self, flags=0):
        return """
<domain type='vmware' id='15'>
    <name>RHEL</name>
    <uuid>564d7cb4-8e3d-06ec-ce82-7b2b13c6a611</uuid>
    <memory unit='KiB'>2097152</memory>
    <currentMemory unit='KiB'>2097152</currentMemory>
    <vcpu placement='static'>1</vcpu>
    <os>
        <type arch='x86_64'>hvm</type>
    </os>
    <clock offset='utc'/>
    <on_poweroff>destroy</on_poweroff>
    <on_reboot>restart</on_reboot>
    <on_crash>destroy</on_crash>
    <devices>
        <disk type='file' device='disk'>
            <source file='[datastore1] RHEL/RHEL.vmdk'/>
            <target dev='sda' bus='scsi'/>
            <address type='drive' controller='0' bus='0' target='0' unit='0'/>
        </disk>
        <controller type='scsi' index='0' model='vmpvscsi'/>
        <interface type='bridge'>
            <mac address='00:0c:29:c6:a6:11'/>
            <source bridge='VM Network'/>
            <model type='vmxnet3'/>
        </interface>
        <video>
            <model type='vmvga' vram='8192'/>
        </video>
    </devices>
</domain>"""


# FIXME: extend vmfakelib allowing to set predefined domain in Connection class
class LibvirtMock(object):
    def close(self):
        pass

    def listAllDomains(self):
        return [VmMock()]

    def storageVolLookupByPath(self, name):
        return LibvirtMock.Volume()

    class Volume(object):
        def info(self):
            return [0, 0, 0]


def hypervisorConnect(uri, username, passwd):
    return LibvirtMock()


def read_ovf(ovf_path):
    return """<?xml version="1.0" encoding="UTF-8"?>
<Envelope xmlns="http://schemas.dmtf.org/ovf/envelope/1"
          xmlns:ovf="http://schemas.dmtf.org/ovf/envelope/1"
          xmlns:rasd="http://schemas.dmtf.org/wbem/wscim/1/cim-schema/2/CIM_Re\
sourceAllocationSettingData">
  <References>
    <File ovf:href="First-disk1.vmdk" ovf:id="file1" ovf:size="349405696"/>
  </References>
  <DiskSection>
    <Disk ovf:capacity="32" ovf:fileRef="file1"/>
  </DiskSection>
  <VirtualSystem ovf:id="First">
    <Name>First</Name>
    <VirtualHardwareSection>
      <Item>
        <rasd:ResourceType>4</rasd:ResourceType>
        <rasd:VirtualQuantity>2048</rasd:VirtualQuantity>
      </Item>
      <Item>
        <rasd:ResourceType>3</rasd:ResourceType>
        <rasd:VirtualQuantity>1</rasd:VirtualQuantity>
      </Item>
      <Item>
        <rasd:Connection>VM Network</rasd:Connection>
        <rasd:ElementName>Ethernet 1</rasd:ElementName>
        <rasd:ResourceSubType>E1000</rasd:ResourceSubType>
        <rasd:ResourceType>10</rasd:ResourceType>
      </Item>
    </VirtualHardwareSection>
  </VirtualSystem>
</Envelope>"""


class v2vTests(TestCaseBase):
    @MonkeyPatch(libvirtconnection, 'open_connection', hypervisorConnect)
    def testGetExternalVMs(self):
        if not v2v.supported():
            raise SkipTest('v2v is not supported current os version')

        vms = v2v.get_external_vms('esx://mydomain', 'user',
                                   ProtectedPassword('password'))['vmList']
        self.assertEquals(len(vms), 1)
        vm = vms[0]
        self.assertEquals(vm['vmId'], '564d7cb4-8e3d-06ec-ce82-7b2b13c6a611')
        self.assertEquals(vm['memSize'], 2048)
        self.assertEquals(vm['smp'], 1)
        self.assertEquals(len(vm['disks']), 1)
        self.assertEquals(len(vm['networks']), 1)
        disk = vm['disks'][0]
        self.assertEquals(disk['dev'], 'sda')
        self.assertEquals(disk['alias'], '[datastore1] RHEL/RHEL.vmdk')
        network = vm['networks'][0]
        self.assertEquals(network['type'], 'bridge')
        self.assertEquals(network['macAddr'], '00:0c:29:c6:a6:11')
        self.assertEquals(network['bridge'], 'VM Network')

    def testOutputParser(self):
        output = ''.join(['[   0.0] Opening the source -i libvirt ://roo...\n',
                          '[   1.0] Creating an overlay to protect the f...\n',
                          '[  88.0] Copying disk 1/2 to /tmp/v2v/0000000...\n',
                          '    (0/100%)\r',
                          '    (50/100%)\r',
                          '    (100/100%)\r',
                          '[ 180.0] Copying disk 2/2 to /tmp/v2v/100000-...\n',
                          '    (0/100%)\r',
                          '    (50/100%)\r',
                          '    (100/100%)\r',
                          '[ 256.0] Creating output metadata',
                          '[ 256.0] Finishing off'])

        parser = v2v.OutputParser()
        events = list(parser.parse(StringIO(output)))
        self.assertEqual(events, [
            (v2v.ImportProgress(1, 2, 'Copying disk 1/2')),
            (v2v.DiskProgress(0)),
            (v2v.DiskProgress(50)),
            (v2v.DiskProgress(100)),
            (v2v.ImportProgress(2, 2, 'Copying disk 2/2')),
            (v2v.DiskProgress(0)),
            (v2v.DiskProgress(50)),
            (v2v.DiskProgress(100))])

    @MonkeyPatch(v2v, '_read_ovf_from_ova', read_ovf)
    def testGetOvaInfo(self):
        ret = v2v.get_ova_info("dummy")
        vm = ret['vmList']
        self.assertEquals(vm['vmName'], 'First')
        self.assertEquals(vm['memSize'], 2048)
        self.assertEquals(vm['smp'], 1)

        disk = vm['disks'][0]
        self.assertEquals(disk['allocation'], '349405696')
        self.assertEquals(disk['capacity'], '34359738368')
        self.assertEquals(disk['type'], 'disk')
        self.assertEquals(disk['alias'], 'First-disk1.vmdk')

        network = vm['networks'][0]
        self.assertEquals(network['bridge'], 'VM Network')
        self.assertEquals(network['model'], 'E1000')
        self.assertEquals(network['type'], 'bridge')
        self.assertEquals(network['dev'], 'Ethernet 1')
