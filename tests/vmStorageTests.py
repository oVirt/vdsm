#
# Copyright 2015 Red Hat, Inc.
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

from virt.vmdevices.storage import Drive
from testlib import VdsmTestCase
from testlib import XMLTestCase
from testlib import permutations, expandPermutations


class DriveXMLTests(XMLTestCase):

    def test_cdrom(self):
        conf = drive_config(
            device='cdrom',
            iface='ide',
            index='2',
            path='/path/to/fedora.iso',
            readonly='True',
        )
        xml = """
            <disk device="cdrom" snapshot="no" type="file">
                <source file="/path/to/fedora.iso" startupPolicy="optional"/>
                <target bus="ide" dev="hdc"/>
                <readonly/>
                <serial>54-a672-23e5b495a9ea</serial>
            </disk>
            """
        self.check({}, conf, xml, is_block_device=False)

    def test_disk_virtio_cache(self):
        conf = drive_config(
            format='cow',
            propagateErrors='on',
            shared='shared',
            specParams={
                'ioTune': {
                    'read_bytes_sec': 6120000,
                    'total_iops_sec': 800,
                }
            },
        )
        xml = """
            <disk device="disk" snapshot="no" type="file">
                <source file="/path/to/volume"/>
                <target bus="virtio" dev="vda"/>
                <shareable/>
                <serial>54-a672-23e5b495a9ea</serial>
                <driver cache="writethrough" error_policy="enospace"
                        io="threads" name="qemu" type="qcow2"/>
                <iotune>
                    <read_bytes_sec>6120000</read_bytes_sec>
                    <total_iops_sec>800</total_iops_sec>
                </iotune>
            </disk>
            """
        vm_conf = {'custom': {'viodiskcache': 'writethrough'}}
        self.check(vm_conf, conf, xml, is_block_device=False)

    def test_disk_block(self):
        conf = drive_config()
        xml = """
            <disk device="disk" snapshot="no" type="block">
                <source dev="/path/to/volume"/>
                <target bus="virtio" dev="vda"/>
                <serial>54-a672-23e5b495a9ea</serial>
                <driver cache="none" error_policy="stop"
                        io="native" name="qemu" type="raw"/>
            </disk>
            """
        self.check({}, conf, xml, is_block_device=True)

    def test_disk_file(self):
        conf = drive_config()
        xml = """
            <disk device="disk" snapshot="no" type="file">
                <source file="/path/to/volume"/>
                <target bus="virtio" dev="vda"/>
                <serial>54-a672-23e5b495a9ea</serial>
                <driver cache="none" error_policy="stop"
                        io="threads" name="qemu" type="raw"/>
            </disk>
            """
        self.check({}, conf, xml, is_block_device=False)

    def test_lun(self):
        conf = drive_config(
            device='lun',
            iface='scsi',
            path='/dev/mapper/lun1',
            sgio='unfiltered',
        )
        xml = """
            <disk device="lun" sgio="unfiltered" snapshot="no" type="block">
                <source dev="/dev/mapper/lun1"/>
                <target bus="scsi" dev="sda"/>
                <serial>54-a672-23e5b495a9ea</serial>
                <driver cache="none" error_policy="stop"
                        io="native" name="qemu" type="raw"/>
            </disk>
            """
        self.check({}, conf, xml, is_block_device=True)

    def check(self, vm_conf, device_conf, xml, is_block_device=False):
        drive = Drive(vm_conf, self.log, **device_conf)
        # Patch to skip the block device checking.
        drive._blockDev = is_block_device
        self.assertXMLEqual(drive.getXML().toxml(), xml)


@expandPermutations
class DriveExSharedStatusTests(VdsmTestCase):

    def test_default_not_shared(self):
        self.check(None, 'none')

    @permutations([['exclusive'], ['shared'], ['none'], ['transient']])
    def test_supported(self, shared):
        self.check(shared, shared)

    def test_unsupported(self):
        self.assertRaises(ValueError, self.check, "UNKNOWN-VALUE", None)

    @permutations([[True], ['True'], ['true']])
    def test_bc_shared(self, shared):
        self.check(shared, 'shared')

    @permutations([[False], ['False'], ['false']])
    def test_bc_not_shared(self, shared):
        self.check(shared, 'none')

    def check(self, shared, expected):
        conf = drive_config()
        if shared:
            conf['shared'] = shared
        drive = Drive({}, self.log, **conf)
        self.assertEqual(drive.extSharedState, expected)


def drive_config(**kw):
    """ Reutrn drive configuration updated from **kw """
    conf = {
        'device': 'disk',
        'format': 'raw',
        'iface': 'virtio',
        'index': '0',
        'path': '/path/to/volume',
        'propagateErrors': 'off',
        'readonly': 'False',
        'serial': '54-a672-23e5b495a9ea',
        'shared': 'none',
        'type': 'disk',
    }
    conf.update(kw)
    return conf
