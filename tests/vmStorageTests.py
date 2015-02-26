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

from monkeypatch import MonkeyPatch
from testlib import VdsmTestCase
from testlib import XMLTestCase
from testlib import permutations, expandPermutations

from vdsm import constants
from vdsm import utils
from virt.vmdevices.storage import Drive


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


class DriveDiskTypeTests(VdsmTestCase):

    def test_cdrom(self):
        conf = drive_config(device='cdrom')
        drive = Drive({}, self.log, **conf)
        self.assertFalse(drive.networkDev)
        self.assertFalse(drive.blockDev)

    def test_floppy(self):
        conf = drive_config(device='floppy')
        drive = Drive({}, self.log, **conf)
        self.assertFalse(drive.networkDev)
        self.assertFalse(drive.blockDev)

    def test_network_disk(self):
        # This is undocumented interface used by glusterfs
        conf = drive_config(volumeInfo={'volType': 'network'})
        drive = Drive({}, self.log, **conf)
        self.assertTrue(drive.networkDev)
        self.assertFalse(drive.blockDev)

    @MonkeyPatch(utils, 'isBlockDevice', lambda path: True)
    def test_block_disk(self):
        conf = drive_config(device='disk')
        drive = Drive({}, self.log, **conf)
        self.assertFalse(drive.networkDev)
        self.assertTrue(drive.blockDev)

    @MonkeyPatch(utils, 'isBlockDevice', lambda path: False)
    def test_file_disk(self):
        conf = drive_config(device='disk')
        drive = Drive({}, self.log, **conf)
        self.assertFalse(drive.networkDev)
        self.assertFalse(drive.blockDev)

    @MonkeyPatch(utils, 'isBlockDevice', lambda path: False)
    def test_migrate_from_file_to_block(self):
        conf = drive_config(path='/filedomain/volume')
        drive = Drive({}, self.log, **conf)
        self.assertFalse(drive.blockDev)
        # Migrate drive to block domain...
        utils.isBlockDevice = lambda path: True
        drive.path = "/blockdomain/volume"
        self.assertTrue(drive.blockDev)

    @MonkeyPatch(utils, 'isBlockDevice', lambda path: True)
    def test_migrate_from_block_to_file(self):
        conf = drive_config(path='/blockdomain/volume')
        drive = Drive({}, self.log, **conf)
        self.assertTrue(drive.blockDev)
        # Migrate drive to file domain...
        utils.isBlockDevice = lambda path: False
        drive.path = "/filedomain/volume"
        self.assertFalse(drive.blockDev)

    @MonkeyPatch(utils, 'isBlockDevice', lambda path: True)
    def test_migrate_from_block_to_network(self):
        conf = drive_config(path='/blockdomain/volume')
        drive = Drive({}, self.log, **conf)
        self.assertTrue(drive.blockDev)
        # Migrate drive to netowrk (not sure we will support this)...
        drive.path = "rbd:pool/volume"
        drive.volumeInfo = {'volType': 'network'}
        self.assertFalse(drive.blockDev)


@expandPermutations
class ChunkedTests(VdsmTestCase):

    @permutations([
        # device, blockDev, format, chunked
        ('cdrom', True, 'raw', False),
        ('cdrom', False, 'raw', False),
        ('floppy', False, 'raw', False),
        ('disk', False, 'raw', False),
        ('disk', True, 'raw', False),
        ('lun', True, 'raw', False),
        ('disk', True, 'cow', True),
    ])
    def test_chunked(self, device, blockDev, format, chunked):
        conf = drive_config(device=device, format=format)
        drive = Drive({}, self.log, **conf)
        drive._blockDev = blockDev
        self.assertEqual(drive.chunked, chunked)


@expandPermutations
class DriveVolumeSizeTests(VdsmTestCase):

    CAPACITY = 8192 * constants.MEGAB

    @permutations([[1024 * constants.MEGAB], [2048 * constants.MEGAB]])
    def test_next_size(self, cursize):
        conf = drive_config(format='cow')
        drive = Drive({}, self.log, **conf)
        self.assertEqual(drive.getNextVolumeSize(cursize, self.CAPACITY),
                         cursize / constants.MEGAB + drive.volExtensionChunk)

    @permutations([[CAPACITY - 1], [CAPACITY], [CAPACITY + 1]])
    def test_next_size_limit(self, cursize):
        conf = drive_config(format='cow')
        drive = Drive({}, self.log, **conf)
        self.assertEqual(drive.getNextVolumeSize(cursize, self.CAPACITY),
                         drive.getMaxVolumeSize(self.CAPACITY))

    def test_max_size(self):
        conf = drive_config(format='cow')
        drive = Drive({}, self.log, **conf)
        size = int(self.CAPACITY * drive.VOLWM_COW_OVERHEAD)
        self.assertEqual(drive.getMaxVolumeSize(self.CAPACITY),
                         size / constants.MEGAB + 1)


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
