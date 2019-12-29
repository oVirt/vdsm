#
# Copyright 2015-2017 Red Hat, Inc.
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

import os
import xml.etree.ElementTree as etree

from collections import namedtuple
from contextlib import contextmanager

from monkeypatch import MonkeyPatch
from testlib import VdsmTestCase
from testlib import XMLTestCase
from testlib import make_config
from testlib import make_file
from testlib import namedTemporaryDir
from testlib import permutations, expandPermutations
from testValidation import xfail

from vdsm.common import exception
from vdsm.common.units import MiB, GiB
from vdsm.common import xmlutils
from vdsm import utils
from vdsm.virt.vmdevices import storage
from vdsm.virt.vmdevices.storage import Drive, DISK_TYPE, DRIVE_SHARED_TYPE
from vdsm.virt.vmdevices.storage import BLOCK_THRESHOLD

VolumeChainEnv = namedtuple(
    'VolumeChainEnv',
    ['drive', 'top', 'base']
)


@expandPermutations
class DriveXMLTests(XMLTestCase):

    @permutations([
        # propagateErrors
        (None,),
        ('off',),
    ])
    def test_cdrom(self, propagateErrors):
        conf = drive_config(
            propagateErrors=propagateErrors,
            device='cdrom',
            iface='ide',
            index='2',
            path='/path/to/fedora.iso',
            readonly='True',
            serial='54-a672-23e5b495a9ea',
            diskType=DISK_TYPE.FILE,
        )
        xml = """
            <disk device="cdrom" snapshot="no" type="file">
                <source file="/path/to/fedora.iso" startupPolicy="optional">
                    <seclabel model="dac" relabel="no" type="none" />
                </source>
                <target bus="ide" dev="hdc"/>
                <readonly/>
                <serial>54-a672-23e5b495a9ea</serial>
                <driver error_policy="stop"
                    name="qemu" type="raw" />
            </disk>
            """
        self.check(conf, xml)

    def test_disk_virtio_cache(self):
        conf = drive_config(
            format='cow',
            propagateErrors='on',
            serial='54-a672-23e5b495a9ea',
            shared='shared',
            iotune={
                'read_bytes_sec': 6120000,
                'total_iops_sec': 800,
            },
            vm_custom={
                'viodiskcache': 'writethrough'
            },
            diskType=DISK_TYPE.FILE,
        )
        xml = """
            <disk device="disk" snapshot="no" type="file">
                <source file="/path/to/volume">
                    <seclabel model="dac" relabel="no" type="none" />
                </source>
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
        self.check(conf, xml)

    def test_disk_param_cache(self):
        conf = drive_config(
            format='raw',
            iface='sata',  # virtio has special treatment - don't use it here
            propagateErrors='on',
            serial='54-a672-23e5b495a9ea',
            cache='writethrough',
            diskType=DISK_TYPE.FILE,
        )
        xml = """
            <disk device="disk" snapshot="no" type="file">
                <source file="/path/to/volume">
                    <seclabel model="dac" relabel="no" type="none" />
                </source>
                <target bus="sata" dev="sda"/>
                <serial>54-a672-23e5b495a9ea</serial>
                <driver cache="writethrough" error_policy="enospace"
                        io="threads" name="qemu" type="raw"/>
            </disk>
            """
        self.check(conf, xml)

    def test_disk_block(self):
        conf = drive_config(
            serial='54-a672-23e5b495a9ea',
            diskType=DISK_TYPE.BLOCK,
        )
        xml = """
            <disk device="disk" snapshot="no" type="block">
                <source dev="/path/to/volume">
                    <seclabel model="dac" relabel="no" type="none" />
                </source>
                <target bus="virtio" dev="vda"/>
                <serial>54-a672-23e5b495a9ea</serial>
                <driver cache="none" error_policy="stop"
                        io="native" name="qemu" type="raw"/>
            </disk>
            """
        self.check(conf, xml)

    def test_disk_with_user_alias(self):
        conf = drive_config(
            serial='54-a672-23e5b495a9ea',
            diskType=DISK_TYPE.BLOCK,
            alias='ua-58ca6050-03d7-00e7-0062-00000000018f',
        )
        xml = """
            <disk device="disk" snapshot="no" type="block">
                <source dev="/path/to/volume">
                    <seclabel model="dac" relabel="no" type="none" />
                </source>
                <target bus="virtio" dev="vda"/>
                <serial>54-a672-23e5b495a9ea</serial>
                <driver cache="none" error_policy="stop"
                        io="native" name="qemu" type="raw"/>
                <alias name="ua-58ca6050-03d7-00e7-0062-00000000018f"/>
            </disk>
            """
        self.check(conf, xml)

    def test_disk_with_discard_on(self):
        conf = drive_config(
            serial='54-a672-23e5b495a9ea',
            discard=True,
            diskType=DISK_TYPE.BLOCK,
        )
        xml = """
            <disk device="disk" snapshot="no" type="block">
                <source dev="/path/to/volume">
                    <seclabel model="dac" relabel="no" type="none" />
                </source>
                <target bus="virtio" dev="vda"/>
                <serial>54-a672-23e5b495a9ea</serial>
                <driver cache="none" discard="unmap" error_policy="stop"
                        io="native" name="qemu" type="raw"/>
            </disk>
            """
        self.check(conf, xml)

    def test_disk_with_discard_off(self):
        conf = drive_config(
            serial='54-a672-23e5b495a9ea',
            discard=False,
            diskType=DISK_TYPE.BLOCK,
        )
        xml = """
            <disk device="disk" snapshot="no" type="block">
                <source dev="/path/to/volume">
                    <seclabel model="dac" relabel="no" type="none" />
                </source>
                <target bus="virtio" dev="vda"/>
                <serial>54-a672-23e5b495a9ea</serial>
                <driver cache="none" error_policy="stop"
                        io="native" name="qemu" type="raw"/>
            </disk>
            """
        self.check(conf, xml)

    def test_disk_file(self):
        conf = drive_config(
            serial='54-a672-23e5b495a9ea',
            diskType=DISK_TYPE.FILE,
        )
        xml = """
            <disk device="disk" snapshot="no" type="file">
                <source file="/path/to/volume">
                    <seclabel model="dac" relabel="no" type="none" />
                </source>
                <target bus="virtio" dev="vda"/>
                <serial>54-a672-23e5b495a9ea</serial>
                <driver cache="none" error_policy="stop"
                        io="threads" name="qemu" type="raw"/>
            </disk>
            """
        self.check(conf, xml)

    def test_lun(self):
        conf = drive_config(
            device='lun',
            iface='scsi',
            path='/dev/mapper/lun1',
            serial='54-a672-23e5b495a9ea',
            sgio='unfiltered',
            diskType=DISK_TYPE.BLOCK,
        )
        xml = """
            <disk device="lun" sgio="unfiltered" snapshot="no" type="block">
                <source dev="/dev/mapper/lun1">
                    <seclabel model="dac" relabel="no" type="none" />
                </source>
                <target bus="scsi" dev="sda"/>
                <driver cache="none" error_policy="stop"
                        io="native" name="qemu" type="raw"/>
            </disk>
            """
        self.check(conf, xml)

    def test_network(self):
        conf = drive_config(
            diskType=DISK_TYPE.NETWORK,
            hosts=[
                dict(name='1.2.3.41', port='6789', transport='tcp'),
                dict(name='1.2.3.42', port='6789', transport='tcp'),
            ],
            path='poolname/volumename',
            protocol='rbd',
            serial='54-a672-23e5b495a9ea',
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
        self.check(conf, xml)

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
            serial='54-a672-23e5b495a9ea',
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
        self.check(conf, xml)

    def test_cdrom_without_serial(self):
        conf = drive_config(
            device='cdrom',
            iface='ide',
            index='2',
            path='/path/to/fedora.iso',
            readonly='True',
            diskType=DISK_TYPE.FILE,
        )
        xml = """
            <disk device="cdrom" snapshot="no" type="file">
                <source file="/path/to/fedora.iso" startupPolicy="optional">
                    <seclabel model="dac" relabel="no" type="none" />
                </source>
                <target bus="ide" dev="hdc"/>
                <readonly/>
                <driver error_policy="stop" name="qemu" type="raw" />
            </disk>
            """
        self.check(conf, xml)

    def test_disk_without_serial(self):
        conf = drive_config(diskType=DISK_TYPE.FILE)
        xml = """
            <disk device="disk" snapshot="no" type="file">
                <source file="/path/to/volume">
                    <seclabel model="dac" relabel="no" type="none" />
                </source>
                <target bus="virtio" dev="vda"/>
                <driver cache="none" error_policy="stop"
                        io="threads" name="qemu" type="raw"/>
            </disk>
            """
        self.check(conf, xml)

    def check(self, device_conf, xml):
        drive = Drive(self.log, **device_conf)
        self.assertXMLEqual(xmlutils.tostring(drive.getXML()), xml)


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
                <source dev="/path/to/replica">
                    <seclabel model="dac" relabel="no" type="none" />
                </source>
                <driver cache="none" error_policy="stop"
                        io="native" name="qemu" type="qcow2"/>
            </disk>
            """
        self.check(conf, xml, diskType=DISK_TYPE.BLOCK)

    def test_block_to_file(self):
        conf = drive_config(
            format='cow',
            diskReplicate=replica(DISK_TYPE.FILE),
        )
        # source: type=file
        # driver: io=threads
        xml = """
            <disk device="disk" snapshot="no" type="file">
                <source file="/path/to/replica">
                    <seclabel model="dac" relabel="no" type="none" />
                </source>
                <driver cache="none" error_policy="stop"
                        io="threads" name="qemu" type="qcow2"/>
            </disk>
            """
        self.check(conf, xml, diskType=DISK_TYPE.FILE)

    def test_file_to_file(self):
        conf = drive_config(
            format='cow',
            diskReplicate=replica(DISK_TYPE.FILE),
        )
        # source: type=file
        # driver: io=threads
        xml = """
            <disk device="disk" snapshot="no" type="file">
                <source file="/path/to/replica">
                    <seclabel model="dac" relabel="no" type="none" />
                </source>
                <driver cache="none" error_policy="stop"
                        io="threads" name="qemu" type="qcow2"/>
            </disk>
            """
        self.check(conf, xml, diskType=DISK_TYPE.FILE)

    def test_file_to_block(self):
        conf = drive_config(
            format='cow',
            diskReplicate=replica(DISK_TYPE.BLOCK),
        )
        # source: type=block
        # driver: io=native
        xml = """
            <disk device="disk" snapshot="no" type="block">
                <source dev="/path/to/replica">
                    <seclabel model="dac" relabel="no" type="none" />
                </source>
                <driver cache="none" error_policy="stop"
                        io="native" name="qemu" type="qcow2"/>
            </disk>
            """
        self.check(conf, xml, diskType=DISK_TYPE.FILE)

    def check(self, device_conf, xml, diskType=DISK_TYPE.FILE):
        drive = Drive(self.log, diskType=diskType, **device_conf)
        self.assertXMLEqual(xmlutils.tostring(drive.getReplicaXML()), xml)


@expandPermutations
class DriveValidation(VdsmTestCase):

    @permutations([["disk"], ["cdrom"], ["floppy"]])
    def test_sgio_without_lun(self, device):
        self.check(device=device, diskType=DISK_TYPE.FILE, sgio='unfiltered')

    def test_cow_with_lun(self):
        self.check(device='lun', diskType=DISK_TYPE.BLOCK, format='cow')

    def test_network_disk_no_hosts(self):
        self.check(diskType=DISK_TYPE.NETWORK, protocol='rbd')

    def test_network_disk_no_protocol(self):
        self.check(diskType=DISK_TYPE.NETWORK, hosts=[{}])

    @permutations([
        # iotune
        [{'total_bytes_sec': -2}],
        [{'write_bytes_sec': 'a'}],
        [{'bogus_setting': 1}],
    ])
    def test_set_iotune(self, iotune):
        conf = drive_config(
            serial='54-a672-23e5b495a9ea',
            diskType=DISK_TYPE.BLOCK,
        )
        drive = Drive(self.log, **conf)

        with self.assertRaises(Exception):
            drive.iotune = iotune

    def check(self, **kw):
        conf = drive_config(**kw)
        drive = Drive(self.log, **conf)
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
        conf = drive_config(diskType=DISK_TYPE.FILE)
        if shared:
            conf['shared'] = shared
        drive = Drive(self.log, **conf)
        self.assertEqual(drive.extSharedState, expected)


@expandPermutations
class DriveDiskTypeTests(VdsmTestCase):

    def test_floppy_file(self):
        conf = drive_config(device="floppy")
        drive = Drive(self.log, diskType=DISK_TYPE.FILE, **conf)
        self.assertEqual(DISK_TYPE.FILE, drive.diskType)

    @permutations([[DISK_TYPE.BLOCK], [DISK_TYPE.NETWORK]])
    def test_floppy_create_invalid_diskType(self, diskType):
        conf = drive_config(device='floppy', diskType=diskType)
        with self.assertRaises(exception.UnsupportedOperation):
            Drive(self.log, **conf)

    @permutations([[DISK_TYPE.BLOCK], [DISK_TYPE.NETWORK]])
    def test_floppy_set_invalid_diskType(self, diskType):
        conf = drive_config(device='floppy')
        drive = Drive(self.log, **conf)
        with self.assertRaises(exception.UnsupportedOperation):
            drive.diskType = diskType

    def test_network_disk(self):
        conf = drive_config(diskType=DISK_TYPE.NETWORK)
        drive = Drive(self.log, **conf)
        self.assertEqual(DISK_TYPE.NETWORK, drive.diskType)

    def test_block_disk(self):
        conf = drive_config(device='disk')
        drive = Drive(self.log, diskType=DISK_TYPE.BLOCK, **conf)
        self.assertEqual(DISK_TYPE.BLOCK, drive.diskType)

    def test_block_cdrom(self):
        conf = drive_config(device='cdrom')
        drive = Drive(self.log, diskType=DISK_TYPE.BLOCK, **conf)
        self.assertEqual(DISK_TYPE.BLOCK, drive.diskType)

    def test_file_disk(self):
        conf = drive_config(device='disk')
        drive = Drive(self.log, diskType=DISK_TYPE.FILE, **conf)
        self.assertEqual(DISK_TYPE.FILE, drive.diskType)

    def test_migrate_from_file_to_block(self):
        conf = drive_config(path='/filedomain/volume')
        drive = Drive(self.log, diskType=DISK_TYPE.FILE, **conf)
        # Migrate drive to block domain...
        drive.diskType = DISK_TYPE.BLOCK
        drive.path = "/blockdomain/volume"
        self.assertEqual(DISK_TYPE.BLOCK, drive.diskType)

    def test_migrate_from_block_to_file(self):
        conf = drive_config(path='/blockdomain/volume')
        drive = Drive(self.log, diskType=DISK_TYPE.BLOCK, **conf)
        # Migrate drive to file domain...
        drive.diskType = DISK_TYPE.FILE
        drive.path = "/filedomain/volume"
        self.assertEqual(DISK_TYPE.FILE, drive.diskType)

    def test_migrate_from_block_to_network(self):
        conf = drive_config(path='/blockdomain/volume')
        drive = Drive(self.log, diskType=DISK_TYPE.BLOCK, **conf)
        # Migrate drive to network disk...
        drive.path = "pool/volume"
        drive.diskType = DISK_TYPE.NETWORK
        self.assertEqual(DISK_TYPE.NETWORK, drive.diskType)

    def test_migrate_network_to_block(self):
        conf = drive_config(diskType=DISK_TYPE.NETWORK, path='pool/volume')
        drive = Drive(self.log, **conf)
        # Migrate drive to block domain...
        drive.path = '/blockdomain/volume'
        drive.diskType = DISK_TYPE.BLOCK
        self.assertEqual(DISK_TYPE.BLOCK, drive.diskType)

    def test_set_invalid_type(self):
        conf = drive_config(diskType=DISK_TYPE.NETWORK, path='pool/volume')
        drive = Drive(self.log, **conf)
        with self.assertRaises(exception.UnsupportedOperation):
            drive.diskType = 'bad'

    def test_set_none_type(self):
        conf = drive_config(diskType=DISK_TYPE.NETWORK, path='pool/volume')
        drive = Drive(self.log, **conf)
        with self.assertRaises(exception.UnsupportedOperation):
            drive.diskType = None

    def test_create_invalid_type(self):
        conf = drive_config(diskType='bad', path='pool/volume')
        with self.assertRaises(exception.UnsupportedOperation):
            Drive(self.log, **conf)

    def test_path_change_reset_threshold_state(self):
        conf = drive_config(diskType=DISK_TYPE.BLOCK, path='/old/path')
        drive = Drive(self.log, **conf)
        # Simulating drive in SET state
        drive.threshold_state = BLOCK_THRESHOLD.SET

        drive.path = '/new/path'
        self.assertEqual(drive.threshold_state, BLOCK_THRESHOLD.UNSET)

    def test_block_threshold_set_state(self):
        path = '/old/path'
        conf = drive_config(diskType=DISK_TYPE.BLOCK, path=path)
        drive = Drive(self.log, **conf)
        drive.threshold_state = BLOCK_THRESHOLD.SET

        drive.on_block_threshold(path)
        self.assertEqual(drive.threshold_state, BLOCK_THRESHOLD.EXCEEDED)

    def test_block_threshold_stale_path(self):
        conf = drive_config(diskType=DISK_TYPE.BLOCK, path='/new/path')
        drive = Drive(self.log, **conf)
        drive.threshold_state = BLOCK_THRESHOLD.SET

        drive.on_block_threshold('/old/path')
        self.assertEqual(drive.threshold_state, BLOCK_THRESHOLD.SET)


@expandPermutations
class ChunkedTests(VdsmTestCase):

    @permutations([
        # device, diskType, format, chunked
        ('cdrom', DISK_TYPE.FILE, 'raw', False),
        ('floppy', DISK_TYPE.FILE, 'raw', False),
        ('disk', DISK_TYPE.FILE, 'raw', False),
        ('disk', DISK_TYPE.BLOCK, 'raw', False),
        ('lun', DISK_TYPE.BLOCK, 'raw', False),
        ('disk', DISK_TYPE.BLOCK, 'cow', True),
    ])
    def test_drive(self, device, diskType, format, chunked):
        conf = drive_config(device=device, format=format)
        drive = Drive(self.log, diskType=diskType, **conf)
        self.assertEqual(drive.chunked, chunked)

    @permutations([
        # replica diskType, replica format
        (DISK_TYPE.BLOCK, 'raw'),
        (DISK_TYPE.BLOCK, 'cow'),
    ])
    def test_replica(self, diskType, format):
        conf = drive_config(
            diskReplicate=replica(diskType, format=format),
            diskType=diskType
        )
        drive = Drive(self.log, **conf)
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
        conf = drive_config(
            diskReplicate=replica(diskType, format=format),
            diskType=diskType
        )
        drive = Drive(self.log, **conf)
        self.assertEqual(drive.replicaChunked, chunked)

    def test_no_replica(self):
        conf = drive_config(diskType=DISK_TYPE.FILE)
        drive = Drive(self.log, **conf)
        self.assertEqual(drive.replicaChunked, False)


@expandPermutations
class DriveVolumeSizeTests(VdsmTestCase):

    CAPACITY = 8 * GiB

    @permutations([[1 * GiB], [2 * GiB]])
    def test_next_size(self, cursize):
        conf = drive_config(format='cow', diskType=DISK_TYPE.BLOCK)
        drive = Drive(self.log, **conf)
        self.assertEqual(drive.getNextVolumeSize(cursize, self.CAPACITY),
                         cursize + drive.volExtensionChunk)

    @permutations([[CAPACITY - 1], [CAPACITY], [CAPACITY + 1]])
    def test_next_size_limit(self, cursize):
        conf = drive_config(format='cow', diskType=DISK_TYPE.BLOCK)
        drive = Drive(self.log, **conf)
        self.assertEqual(drive.getNextVolumeSize(cursize, self.CAPACITY),
                         drive.getMaxVolumeSize(self.CAPACITY))

    def test_max_size(self):
        conf = drive_config(format='cow', diskType=DISK_TYPE.BLOCK)
        drive = Drive(self.log, **conf)
        size = utils.round(self.CAPACITY * drive.VOLWM_COW_OVERHEAD, MiB)
        self.assertEqual(drive.getMaxVolumeSize(self.CAPACITY), size)


@expandPermutations
class TestDriveLeases(XMLTestCase):
    """
    To have leases, drive must have a non-empty volumeChain,
    shared="exclusive", or shared="false" and irs:use_volume_leases=True.

    Any other setting results in no leases.
    """

    # Drive without leases

    @MonkeyPatch(storage, 'config', make_config([
        ("irs", "use_volume_leases", "false")
    ]))
    @permutations([
        ["true"],
        ["True"],
        ["TRUE"],
        ["false"],
        ["False"],
        ["FALSE"],
        [DRIVE_SHARED_TYPE.NONE],
        [DRIVE_SHARED_TYPE.EXCLUSIVE],
        [DRIVE_SHARED_TYPE.SHARED],
        [DRIVE_SHARED_TYPE.TRANSIENT],
    ])
    def test_shared_no_volume_leases_no_chain(self, shared):
        conf = drive_config(shared=shared, volumeChain=[])
        self.check_no_leases(conf)

    @MonkeyPatch(storage, 'config', make_config([
        ("irs", "use_volume_leases", "true")
    ]))
    @permutations([
        ["true"],
        ["True"],
        ["TRUE"],
        ["false"],
        ["False"],
        ["FALSE"],
        [DRIVE_SHARED_TYPE.NONE],
        [DRIVE_SHARED_TYPE.EXCLUSIVE],
        [DRIVE_SHARED_TYPE.SHARED],
        [DRIVE_SHARED_TYPE.TRANSIENT],
    ])
    def test_shared_use_volume_leases_no_chain(self, shared):
        conf = drive_config(shared=shared, volumeChain=[])
        self.check_no_leases(conf)

    # Drive with leases

    @MonkeyPatch(storage, 'config', make_config([
        ("irs", "use_volume_leases", "true")
    ]))
    @permutations([
        ["false"],
        [DRIVE_SHARED_TYPE.EXCLUSIVE],
    ])
    def test_use_volume_leases(self, shared):
        conf = drive_config(shared=shared, volumeChain=make_volume_chain())
        self.check_leases(conf)

    @MonkeyPatch(storage, 'config', make_config([
        ("irs", "use_volume_leases", "false")
    ]))
    @permutations([
        [DRIVE_SHARED_TYPE.EXCLUSIVE],
    ])
    def test_no_volume_leases(self, shared):
        conf = drive_config(shared=shared, volumeChain=make_volume_chain())
        self.check_leases(conf)

    # Helpers

    def check_no_leases(self, conf):
        drive = Drive(self.log, diskType=DISK_TYPE.FILE, **conf)
        leases = list(drive.getLeasesXML())
        self.assertEqual([], leases)

    def check_leases(self, conf):
        drive = Drive(self.log, diskType=DISK_TYPE.FILE, **conf)
        leases = list(drive.getLeasesXML())
        self.assertEqual(1, len(leases))
        xml = """
        <lease>
            <key>vol_id</key>
            <lockspace>dom_id</lockspace>
            <target offset="0" path="path" />
        </lease>
        """
        self.assertXMLEqual(xmlutils.tostring(leases[0]), xml)


@expandPermutations
class TestDriveNaming(VdsmTestCase):

    @permutations([
        ['ide', 0, 'hda'],
        ['ide', 1, 'hdb'],
        ['ide', 2, 'hdc'],
        ['ide', 25, 'hdz'],
        ['ide', 26, 'hdaa'],
        ['ide', 27, 'hdab'],

        ['scsi', 0, 'sda'],
        ['scsi', 1, 'sdb'],
        ['scsi', 2, 'sdc'],
        ['scsi', 25, 'sdz'],
        ['scsi', 26, 'sdaa'],
        ['scsi', 27, 'sdab'],

        ['virtio', 0, 'vda'],
        ['virtio', 1, 'vdb'],
        ['virtio', 2, 'vdc'],
        ['virtio', 25, 'vdz'],
        ['virtio', 26, 'vdaa'],
        ['virtio', 27, 'vdab'],

        ['fdc', 0, 'fda'],
        ['fdc', 1, 'fdb'],
        ['fdc', 2, 'fdc'],
        ['fdc', 25, 'fdz'],
        ['fdc', 26, 'fdaa'],
        ['fdc', 27, 'fdab'],

        ['sata', 0, 'sda'],
        ['sata', 1, 'sdb'],
        ['sata', 2, 'sdc'],
        ['sata', 25, 'sdz'],
        ['sata', 26, 'sdaa'],
        ['sata', 27, 'sdab'],
    ])
    def test_ide_drive(self, interface, index, expected_name):
        conf = drive_config(
            device='disk',
            iface=interface,
            index=index,
            diskType=DISK_TYPE.FILE
        )

        drive = Drive(self.log, **conf)
        self.assertEqual(drive.name, expected_name)

    @permutations([
        ['ide', -1],
        ['scsi', -1],
        ['virtio', -1],
        ['fdc', -1],
        ['sata', -1],
    ])
    def test_invalid_name(self, interface, index):
        conf = drive_config(
            device='disk',
            iface=interface,
            index=index,
            diskType=DISK_TYPE.FILE
        )

        self.assertRaises(ValueError, Drive, self.log, **conf)


class TestVolumeTarget(VdsmTestCase):
    def setUp(self):
        volume_chain = [{"path": "/top",
                         "volumeID": "00000000-0000-0000-0000-000000000000"},
                        {"path": "/base",
                         "volumeID": "11111111-1111-1111-1111-111111111111"}]
        self.conf = drive_config(volumeChain=volume_chain,
                                 alias=0,
                                 name="name")

        self.actual_chain = [
            storage.VolumeChainEntry(
                uuid="11111111-1111-1111-1111-111111111111",
                index=1,
                path=None,
                allocation=None
            ),
            storage.VolumeChainEntry(
                uuid="00000000-0000-0000-0000-000000000000",
                index=None,
                path=None,
                allocation=None
            )
        ]

    def test_base_not_found(self):
        drive = Drive(self.log, diskType=DISK_TYPE.FILE, **self.conf)
        with self.assertRaises(storage.VolumeNotFound):
            drive.volume_target("FFFFFFFF-FFFF-FFFF-FFFF-111111111111",
                                self.actual_chain)

    def test_internal_volume(self):
        drive = Drive(self.log, diskType=DISK_TYPE.NETWORK, **self.conf)
        actual = drive.volume_target(
            "11111111-1111-1111-1111-111111111111",
            self.actual_chain)
        self.assertEqual(actual, "vda[1]")

    def test_top_volume(self):
        drive = Drive(self.log, diskType=DISK_TYPE.NETWORK, **self.conf)
        actual = drive.volume_target(
            "00000000-0000-0000-0000-000000000000",
            self.actual_chain)
        self.assertEqual(actual, None)

    def test_volume_missing(self):
        drive = Drive(self.log, diskType=DISK_TYPE.NETWORK, **self.conf)
        with self.assertRaises(storage.VolumeNotFound):
            drive.volume_target(
                "FFFFFFFF-FFFF-FFFF-FFFF-000000000000",
                self.actual_chain)


class TestVolumeChain(VdsmTestCase):
    @contextmanager
    def make_env(self, disk_type):
        with namedTemporaryDir() as tmpdir:
            """
            Below we imitate that behaviour by providing
            two different directories under /rhv/data-center
            root and one of those directories
            is a symlink to another one.

            We fill VolumeChain with real directory and
            use symlinked directory in XML, emulating
            libvirt reply.
            """
            dc_base = os.path.join(tmpdir, "dc")
            run_base = os.path.join(tmpdir, "run")
            images_path = os.path.join(dc_base, "images")

            os.makedirs(images_path)
            os.symlink(dc_base, run_base)

            dc_top_vol = os.path.join(
                images_path,
                "11111111-1111-1111-1111-111111111111")
            dc_base_vol = os.path.join(
                images_path,
                "22222222-2222-2222-2222-222222222222")

            make_file(dc_top_vol)
            make_file(dc_base_vol)

            run_top_vol = os.path.join(
                run_base,
                "images",
                "11111111-1111-1111-1111-111111111111")
            run_base_vol = os.path.join(
                run_base,
                "images",
                "22222222-2222-2222-2222-222222222222")

            volume_chain = [
                {'path': dc_top_vol,
                 'volumeID': '11111111-1111-1111-1111-111111111111'},
                {'path': dc_base_vol,
                 'volumeID': '22222222-2222-2222-2222-222222222222'}
            ]
            conf = drive_config(volumeChain=volume_chain)
            drive = Drive(self.log, diskType=disk_type, **conf)

            yield VolumeChainEnv(
                drive, run_top_vol,
                run_base_vol
            )

    def test_parse_volume_chain_block(self):
        with self.make_env(DISK_TYPE.BLOCK) as env:
            disk_xml = etree.fromstring("""
            <disk>
                <source dev='%(top)s'>
                    <seclabel model="dac" relabel="no" type="none" />
                </source>
                <backingStore type='block' index='1'>
                    <source dev='%(base)s'/>
                    <backingStore/>
                </backingStore>
            </disk>""" % {
                "top": env.top,
                "base": env.base
            })

            chain = env.drive.parse_volume_chain(disk_xml)
            expected = [
                storage.VolumeChainEntry(
                    path=env.base,
                    allocation=None,
                    uuid='22222222-2222-2222-2222-222222222222',
                    index=1),
                storage.VolumeChainEntry(
                    path=env.top,
                    allocation=None,
                    uuid='11111111-1111-1111-1111-111111111111',
                    index=None)
            ]
            self.assertEqual(chain, expected)

    def test_parse_volume_chain_file(self):
        with self.make_env(DISK_TYPE.FILE) as env:
            disk_xml = etree.fromstring("""
            <disk>
                <source file='%(top)s'>
                    <seclabel model="dac" relabel="no" type="none" />
                </source>
                <backingStore type='file' index='1'>
                    <source file='%(base)s'/>
                    <backingStore/>
                </backingStore>
            </disk>""" % {
                "top": env.top,
                "base": env.base
            })

            chain = env.drive.parse_volume_chain(disk_xml)
            expected = [
                storage.VolumeChainEntry(
                    path=env.base,
                    allocation=None,
                    uuid='22222222-2222-2222-2222-222222222222',
                    index=1),
                storage.VolumeChainEntry(
                    path=env.top,
                    allocation=None,
                    uuid='11111111-1111-1111-1111-111111111111',
                    index=None)
            ]
            self.assertEqual(chain, expected)

    def test_parse_volume_chain_network(self):
        volume_chain = [
            {'path': 'server:/vol/11111111-1111-1111-1111-111111111111',
             'volumeID': '11111111-1111-1111-1111-111111111111'},
            {'path': 'server:/vol/22222222-2222-2222-2222-222222222222',
             'volumeID': '22222222-2222-2222-2222-222222222222'}
        ]
        conf = drive_config(volumeChain=volume_chain)
        drive = Drive(self.log, diskType=DISK_TYPE.NETWORK, **conf)

        disk_xml = etree.fromstring("""
        <disk>
            <source name='server:/vol/11111111-1111-1111-1111-111111111111'>
                <seclabel model="dac" relabel="no" type="none" />
            </source>
            <backingStore type='network' index='1'>
                <source
                    name='server:/vol/22222222-2222-2222-2222-222222222222'/>
                <backingStore/>
            </backingStore>
        </disk>""")

        chain = drive.parse_volume_chain(disk_xml)
        expected = [
            storage.VolumeChainEntry(
                path='server:/vol/22222222-2222-2222-2222-222222222222',
                allocation=None,
                uuid='22222222-2222-2222-2222-222222222222',
                index=1),
            storage.VolumeChainEntry(
                path='server:/vol/11111111-1111-1111-1111-111111111111',
                allocation=None,
                uuid='11111111-1111-1111-1111-111111111111',
                index=None)
        ]
        self.assertEqual(chain, expected)

    def test_parse_volume_not_in_chain(self):
        with self.make_env(DISK_TYPE.BLOCK) as env:
            disk_xml = etree.fromstring("""
            <disk>
                <source dev='/top'>
                    <seclabel model="dac" relabel="no" type="none" />
                </source>
                <backingStore type='block' index='1'>
                    <format type='raw'/>
                    <source dev='/base'/>
                    <backingStore/>
                </backingStore>
            </disk>""")

            with self.assertRaises(LookupError):
                env.drive.parse_volume_chain(disk_xml)

    def test_parse_volume_no_source(self):
        with self.make_env(DISK_TYPE.BLOCK) as env:
            disk_xml = etree.fromstring("""<disk/>""")

            chain = env.drive.parse_volume_chain(disk_xml)
            self.assertIsNone(chain)

    @xfail("it returns None, instead of a one item chain - looks like a bug")
    def test_parse_volume_no_backing(self):
        with self.make_env(DISK_TYPE.BLOCK) as env:
            disk_xml = etree.fromstring("""
            <disk>
                <source dev='%s'>
                    <seclabel model="dac" relabel="no" type="none" />
                </source>
            </disk>""" % env.top)

            chain = env.drive.parse_volume_chain(disk_xml)
            expected = [
                storage.VolumeChainEntry(
                    path=env.top,
                    allocation=None,
                    uuid='11111111-1111-1111-1111-111111111111')
            ]
            self.assertEqual(chain, expected)

    def test_parse_volume_chain_no_index(self):
        with self.make_env(DISK_TYPE.BLOCK) as env:
            disk_xml = etree.fromstring("""
            <disk>
                <source dev='%(top)s'>
                    <seclabel model="dac" relabel="no" type="none" />
                </source>
                <backingStore type='block'>
                    <source dev='%(base)s'/>
                    <backingStore/>
                </backingStore>
            </disk>""" % {
                "top": env.top,
                "base": env.base
            })

            with self.assertRaises(storage.InvalidBackingStoreIndex):
                env.drive.parse_volume_chain(disk_xml)

    def test_parse_volume_chain_index_invalid(self):
        with self.make_env(DISK_TYPE.BLOCK) as env:
            disk_xml = etree.fromstring("""
            <disk>
                <source dev='%(top)s'>
                    <seclabel model="dac" relabel="no" type="none" />
                </source>
                <backingStore type='block' index='fail'>
                    <source dev='%(base)s'/>
                    <backingStore/>
                </backingStore>
            </disk>""" % {
                "top": env.top,
                "base": env.base
            })
            with self.assertRaises(storage.InvalidBackingStoreIndex):
                env.drive.parse_volume_chain(disk_xml)


class TestDiskSnapshotXml(XMLTestCase):
    def setUp(self):
        self.conf = drive_config(name="vda")

    def test_file(self):
        drive = Drive(self.log, diskType=DISK_TYPE.FILE, **self.conf)

        expected = """
            <disk name='vda' snapshot='external' type='file'>
                <source file='/image' type='file'>
                    <seclabel model="dac" relabel="no" type="none" />
                </source>
            </disk>
            """
        snap_info = {'path': '/image', 'device': 'disk'}
        actual = drive.get_snapshot_xml(snap_info)
        self.assertXMLEqual(xmlutils.tostring(actual), expected)

    def test_block(self):
        drive = Drive(self.log, diskType=DISK_TYPE.BLOCK, **self.conf)

        expected = """
            <disk name='vda' snapshot='external' type='block'>
                <source dev='/dev/dm-1' type='block'>
                    <seclabel model="dac" relabel="no" type="none" />
                </source>
            </disk>
            """
        snap_info = {'path': '/dev/dm-1', 'device': 'disk'}
        actual = drive.get_snapshot_xml(snap_info)
        self.assertXMLEqual(xmlutils.tostring(actual), expected)

    def test_network(self):
        drive = Drive(self.log, diskType=DISK_TYPE.NETWORK,
                      protocol='gluster', **self.conf)

        expected = """
            <disk name='vda' snapshot='external' type='network'>
                <source protocol='gluster'
                        name='volume/11111111-1111-1111-1111-111111111111'
                        type='network'>
                    <host name="brick1.example.com" port="49152"
                        transport="tcp"/>
                    <host name="brick2.example.com" port="49153"
                        transport="tcp"/>
                </source>
            </disk>
            """
        snap_info = {
            'protocol': 'gluster',
            'path': 'volume/11111111-1111-1111-1111-111111111111',
            'diskType': 'network',
            'device': 'disk',
            'hosts': [
                {
                    'name': 'brick1.example.com',
                    'port': '49152',
                    'transport': 'tcp'
                },
                {
                    'name': 'brick2.example.com',
                    'port': '49153',
                    'transport': 'tcp'
                }
            ]
        }
        actual = drive.get_snapshot_xml(snap_info)
        self.assertXMLEqual(xmlutils.tostring(actual), expected)

    def test_incorrect_disk_type(self):
        drive = Drive(self.log, diskType=DISK_TYPE.FILE, **self.conf)

        with self.assertRaises(exception.UnsupportedOperation):
            drive.get_snapshot_xml({"path": "/foo", "diskType": "bad"})

    def test_incorrect_protocol(self):
        drive = Drive(self.log, diskType=DISK_TYPE.NETWORK,
                      protocol='gluster', **self.conf)

        with self.assertRaises(exception.UnsupportedOperation):
            drive.get_snapshot_xml({'protocol': 'bad', 'diskType': 'network'})


@expandPermutations
class DriveConfigurationTests(VdsmTestCase):

    @permutations([
        # flag, expected
        ['true', True],
        ['false', False],
        [True, True],
        [False, False],
        [None, False]
    ])
    def test_cdrom_readonly(self, flag, expected):
        conf = drive_config(
            readonly=flag,
            device='cdrom',
            iface='ide',
            serial='54-a672-23e5b495a9ea',
            diskType=DISK_TYPE.FILE,
        )
        drive = Drive(self.log, **conf)
        self.assertEqual(drive.device, 'cdrom')
        self.assertIs(drive.readonly, expected)

    @permutations([
        # flag, expected
        ['true', True],
        ['false', False],
        [True, True],
        [False, False],
        [None, False]
    ])
    def test_disk_readonly(self, flag, expected):
        conf = drive_config(
            readonly=flag,
            serial='54-a672-23e5b495a9ea',
            diskType=DISK_TYPE.FILE,
        )
        drive = Drive(self.log, **conf)
        self.assertEqual(drive.device, 'disk')
        self.assertIs(drive.readonly, expected)

    @permutations([
        # flag, expected
        ['true', True],
        ['false', False],
        [True, True],
        [False, False],
        [None, True]
    ])
    def test_floppy_readonly(self, flag, expected):
        conf = drive_config(
            readonly=flag,
            device='floppy'
        )
        drive = Drive(self.log, **conf)
        self.assertEqual(drive.device, 'floppy')
        self.assertIs(drive.readonly, expected)


class TestNeedsMonitoring(VdsmTestCase):

    # Monitoring not needed

    def test_no_need_readonly(self):
        conf = drive_config(diskType=DISK_TYPE.BLOCK, format="cow",
                            readonly=True)
        drive = Drive(self.log, **conf)
        self.assertFalse(drive.needs_monitoring(events_enabled=False))

    def test_no_need_monitoring_disabled(self):
        conf = drive_config(diskType=DISK_TYPE.BLOCK, format="cow")
        drive = Drive(self.log, **conf)
        drive.monitorable = False
        self.assertFalse(drive.needs_monitoring(events_enabled=False))

    def test_no_need_not_chunked(self):
        conf = drive_config(diskType=DISK_TYPE.FILE, format="cow")
        drive = Drive(self.log, **conf)
        self.assertFalse(drive.needs_monitoring(events_enabled=False))

    def test_no_need_replica_not_chunked(self):
        conf = drive_config(diskType=DISK_TYPE.FILE, format="cow")
        drive = Drive(self.log, **conf)
        drive.diskReplicate = replica(DISK_TYPE.FILE, format="cow")
        self.assertFalse(drive.needs_monitoring(events_enabled=False))

    # Monitoring needed

    def test_need_chunked(self):
        conf = drive_config(diskType=DISK_TYPE.BLOCK, format="cow")
        drive = Drive(self.log, **conf)
        self.assertTrue(drive.needs_monitoring(events_enabled=False))

    def test_need_replica_chunked(self):
        conf = drive_config(diskType=DISK_TYPE.FILE, format="cow")
        drive = Drive(self.log, **conf)
        drive.diskReplicate = replica(DISK_TYPE.BLOCK, format="cow")
        self.assertTrue(drive.needs_monitoring(events_enabled=False))


class TestNeedsMonitoringEvents(VdsmTestCase):

    # Monitoring not needed.

    def test_no_need_chunked_threshold_set(self):
        conf = drive_config(diskType=DISK_TYPE.BLOCK, format="cow")
        drive = Drive(self.log, **conf)
        drive.threshold_state = BLOCK_THRESHOLD.SET
        self.assertFalse(drive.needs_monitoring(events_enabled=True))

    def test_no_need_replica_chunked_threshold_set(self):
        conf = drive_config(diskType=DISK_TYPE.FILE, format="cow")
        drive = Drive(self.log, **conf)
        drive.diskReplicate = replica(DISK_TYPE.BLOCK, format="cow")
        drive.threshold_state = BLOCK_THRESHOLD.SET
        self.assertFalse(drive.needs_monitoring(events_enabled=True))

    # Monitoring needed.

    def test_need_chunked_threshold_unset(self):
        conf = drive_config(diskType=DISK_TYPE.BLOCK, format="cow")
        drive = Drive(self.log, **conf)
        self.assertTrue(drive.needs_monitoring(events_enabled=True))

    def test_need_chunked_threshold_exceeded(self):
        conf = drive_config(diskType=DISK_TYPE.BLOCK, format="cow")
        drive = Drive(self.log, **conf)
        drive.threshold_state = BLOCK_THRESHOLD.EXCEEDED
        self.assertTrue(drive.needs_monitoring(events_enabled=True))

    def test_need_replica_chunked_threshold_unset(self):
        conf = drive_config(diskType=DISK_TYPE.FILE, format="cow")
        drive = Drive(self.log, **conf)
        drive.diskReplicate = replica(DISK_TYPE.BLOCK, format="cow")
        self.assertTrue(drive.needs_monitoring(events_enabled=True))

    def test_need_replica_chunked_threshold_exceeded(self):
        conf = drive_config(diskType=DISK_TYPE.FILE, format="cow")
        drive = Drive(self.log, **conf)
        drive.diskReplicate = replica(DISK_TYPE.BLOCK, format="cow")
        drive.threshold_state = BLOCK_THRESHOLD.EXCEEDED
        self.assertTrue(drive.needs_monitoring(events_enabled=True))


def make_volume_chain(path="path", offset=0, vol_id="vol_id", dom_id="dom_id"):
    return [{"leasePath": path,
             "leaseOffset": offset,
             "volumeID": vol_id,
             "domainID": dom_id}]


def drive_config(readonly=None, propagateErrors=None, **kw):
    """ Return drive configuration updated from **kw """
    conf = {
        'device': 'disk',
        'format': 'raw',
        'iface': 'virtio',
        'index': '0',
        'path': '/path/to/volume',
        'shared': 'none',
        'type': 'disk',
    }
    if readonly is not None:
        conf['readonly'] = readonly
    if propagateErrors is not None:
        conf['propagateErrors'] = propagateErrors
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
