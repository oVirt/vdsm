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
from virt.vmdevices.storage import Drive, DISK_TYPE


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
                <driver cache="none" error_policy="stop"
                        io="native" name="qemu" type="raw"/>
            </disk>
            """
        self.check({}, conf, xml, is_block_device=True)

    def test_network(self):
        conf = drive_config(
            diskType=DISK_TYPE.NETWORK,
            hosts=[
                dict(name='1.2.3.41', port='6789', transport='tcp'),
                dict(name='1.2.3.42', port='6789', transport='tcp'),
            ],
            path='poolname/volumename',
            protocol='rbd',
        )
        xml = """
            <disk device="disk" snapshot="no" type="network">
                <source name="poolname/volumename" protocol="rbd">
                    <host name="1.2.3.41" port="6789" transport="tcp"/>
                    <host name="1.2.3.42" port="6789" transport="tcp"/>
                </source>
                <target bus="virtio" dev="vda"/>
                <serial>54-a672-23e5b495a9ea</serial>
                <driver cache="none" error_policy="stop"
                        io="threads" name="qemu" type="raw"/>
            </disk>
            """
        self.check({}, conf, xml, is_block_device=None)

    def test_network_with_auth(self):
        conf = drive_config(
            auth={"type": "ceph", "uuid": "abcdef", "username": "cinder"},
            diskType=DISK_TYPE.NETWORK,
            hosts=[
                dict(name='1.2.3.41', port='6789', transport='tcp'),
                dict(name='1.2.3.42', port='6789', transport='tcp'),
            ],
            path='poolname/volumename',
            protocol='rbd',
        )
        xml = """
            <disk device="disk" snapshot="no" type="network">
                <source name="poolname/volumename" protocol="rbd">
                    <host name="1.2.3.41" port="6789" transport="tcp"/>
                    <host name="1.2.3.42" port="6789" transport="tcp"/>
                </source>
                <auth username="cinder">
                    <secret type="ceph" uuid="abcdef"/>
                </auth>
                <target bus="virtio" dev="vda"/>
                <serial>54-a672-23e5b495a9ea</serial>
                <driver cache="none" error_policy="stop"
                        io="threads" name="qemu" type="raw"/>
            </disk>
            """
        self.check({}, conf, xml, is_block_device=None)

    def check(self, vm_conf, device_conf, xml, is_block_device=False):
        drive = Drive(vm_conf, self.log, **device_conf)
        # Patch to skip the block device checking.
        if is_block_device is not None:
            drive._blockDev = is_block_device
        self.assertXMLEqual(drive.getXML().toxml(), xml)


class DriveReplicaXML(XMLTestCase):

    # Replica XML should match Drive XML using same diskType, cache and
    # propagateErrors settings.  Only the source and driver elements are used
    # by libvirt.
    # https://libvirt.org/html/libvirt-libvirt-domain.html#virDomainBlockCopy

    def test_block_to_block(self):
        conf = drive_config(
            format='cow',
            diskReplicate=replica(DISK_TYPE.BLOCK),
        )
        # source: type=block
        # driver: io=native
        xml = """
            <disk device="disk" snapshot="no" type="block">
                <source dev="/path/to/replica"/>
                <driver cache="none" error_policy="stop"
                        io="native" name="qemu" type="qcow2"/>
            </disk>
            """
        self.check({}, conf, xml, is_block_device=True)

    def test_block_to_file(self):
        conf = drive_config(
            format='cow',
            diskReplicate=replica(DISK_TYPE.FILE),
        )
        # source: type=file
        # driver: io=threads
        xml = """
            <disk device="disk" snapshot="no" type="file">
                <source file="/path/to/replica"/>
                <driver cache="none" error_policy="stop"
                        io="threads" name="qemu" type="qcow2"/>
            </disk>
            """
        self.check({}, conf, xml, is_block_device=True)

    def test_file_to_file(self):
        conf = drive_config(
            format='cow',
            diskReplicate=replica(DISK_TYPE.FILE),
        )
        # source: type=file
        # driver: io=threads
        xml = """
            <disk device="disk" snapshot="no" type="file">
                <source file="/path/to/replica"/>
                <driver cache="none" error_policy="stop"
                        io="threads" name="qemu" type="qcow2"/>
            </disk>
            """
        self.check({}, conf, xml, is_block_device=False)

    def test_file_to_block(self):
        conf = drive_config(
            format='cow',
            diskReplicate=replica(DISK_TYPE.BLOCK),
        )
        # source: type=block
        # driver: io=native
        xml = """
            <disk device="disk" snapshot="no" type="block">
                <source dev="/path/to/replica"/>
                <driver cache="none" error_policy="stop"
                        io="native" name="qemu" type="qcow2"/>
            </disk>
            """
        self.check({}, conf, xml, is_block_device=False)

    def check(self, vm_conf, device_conf, xml, is_block_device=False):
        drive = Drive(vm_conf, self.log, **device_conf)
        # Patch to skip the block device checking.
        drive._blockDev = is_block_device
        self.assertXMLEqual(drive.getReplicaXML().toxml(), xml)


@expandPermutations
class DriveValidation(VdsmTestCase):

    @permutations([["disk"], ["cdrom"], ["floppy"]])
    def test_sgio_without_lun(self, device):
        self.check(device=device, sgio='unfiltered')

    def test_cow_with_lun(self):
        self.check(device='lun', format='cow')

    def test_network_disk_no_hosts(self):
        self.check(diskType=DISK_TYPE.NETWORK, protocol='rbd')

    def test_network_disk_no_protocol(self):
        self.check(diskType=DISK_TYPE.NETWORK, hosts=[{}])

    def check(self, **kw):
        conf = drive_config(**kw)
        drive = Drive({}, self.log, **conf)
        self.assertRaises(ValueError, drive.getXML)


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
        conf = drive_config(diskType=DISK_TYPE.NETWORK)
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
        # Migrate drive to network disk...
        drive.path = "pool/volume"
        drive.diskType = DISK_TYPE.NETWORK
        self.assertFalse(drive.blockDev)

    @MonkeyPatch(utils, 'isBlockDevice', lambda path: True)
    def test_migrate_network_to_block(self):
        conf = drive_config(diskType=DISK_TYPE.NETWORK, path='pool/volume')
        drive = Drive({}, self.log, **conf)
        self.assertTrue(drive.networkDev)
        # Migrate drive to block domain...
        drive.path = '/blockdomain/volume'
        drive.diskType = None
        self.assertTrue(drive.blockDev)


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
    def test_drive(self, device, blockDev, format, chunked):
        conf = drive_config(device=device, format=format)
        drive = Drive({}, self.log, **conf)
        drive._blockDev = blockDev
        self.assertEqual(drive.chunked, chunked)

    @permutations([
        # replica diskType, replica format
        (DISK_TYPE.BLOCK, 'raw'),
        (DISK_TYPE.BLOCK, 'cow'),
    ])
    def test_replica(self, diskType, format):
        conf = drive_config(diskReplicate=replica(diskType, format=format))
        drive = Drive({}, self.log, **conf)
        drive._blockDev = False
        self.assertEqual(drive.chunked, False)


@expandPermutations
class ReplicaChunkedTests(VdsmTestCase):

    @permutations([
        # replica diskType, replica format, chunked
        (DISK_TYPE.FILE, 'raw', False),
        (DISK_TYPE.FILE, 'cow', False),
        (DISK_TYPE.BLOCK, 'raw', False),
        (DISK_TYPE.BLOCK, 'cow', True),
    ])
    def test_replica(self, diskType, format, chunked):
        conf = drive_config(diskReplicate=replica(diskType, format=format))
        drive = Drive({}, self.log, **conf)
        self.assertEqual(drive.replicaChunked, chunked)

    def test_no_replica(self):
        conf = drive_config()
        drive = Drive({}, self.log, **conf)
        self.assertEqual(drive.replicaChunked, False)


@expandPermutations
class DriveVolumeSizeTests(VdsmTestCase):

    CAPACITY = 8192 * constants.MEGAB

    @permutations([[1024 * constants.MEGAB], [2048 * constants.MEGAB]])
    def test_next_size(self, cursize):
        conf = drive_config(format='cow')
        drive = Drive({}, self.log, **conf)
        self.assertEqual(drive.getNextVolumeSize(cursize, self.CAPACITY),
                         cursize + drive.volExtensionChunk)

    @permutations([[CAPACITY - 1], [CAPACITY], [CAPACITY + 1]])
    def test_next_size_limit(self, cursize):
        conf = drive_config(format='cow')
        drive = Drive({}, self.log, **conf)
        self.assertEqual(drive.getNextVolumeSize(cursize, self.CAPACITY),
                         drive.getMaxVolumeSize(self.CAPACITY))

    def test_max_size(self):
        conf = drive_config(format='cow')
        drive = Drive({}, self.log, **conf)
        size = utils.round(self.CAPACITY * drive.VOLWM_COW_OVERHEAD,
                           constants.MEGAB)
        self.assertEqual(drive.getMaxVolumeSize(self.CAPACITY), size)


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


def replica(diskType, format="cow"):
    return {
        "cache": "none",
        "device": "disk",
        "diskType": diskType,
        "format": format,
        "path": "/path/to/replica",
        "propagateErrors": "off",
    }
