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

import v2v
from vdsm import libvirtconnection


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


def hypervisorConnect(uri, username, passwd):
    return LibvirtMock()


class v2vTests(TestCaseBase):
    @MonkeyPatch(libvirtconnection, 'open_connection', hypervisorConnect)
    def testGetExternalVMs(self):
        if not v2v.supported():
            raise SkipTest('v2v is not supported current os version')

        vms = v2v.get_external_vms('esx://mydomain', 'user',
                                   'password')
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
