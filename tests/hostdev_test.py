# SPDX-FileCopyrightText: oVirt Developers
# SPDX-License-Identifier: GPL-2.0-or-later

import logging

import pytest

from vdsm.common import exception
from vdsm.common import xmlutils
from vdsm.virt.vmdevices import network, hwclass

from testlib import VdsmTestCase as TestCaseBase, XMLTestCase
from testlib import permutations, expandPermutations
from monkeypatch import MonkeyClass, MonkeyPatchScope

from vdsm.common import hooks
from vdsm.common import hostdev
from vdsm.common import libvirtconnection

import hostdevlib


@expandPermutations
@MonkeyClass(libvirtconnection, 'get', hostdevlib.Connection)
@MonkeyClass(hostdev, '_sriov_totalvfs', hostdevlib.fake_totalvfs)
@MonkeyClass(hostdev, '_pci_header_type', lambda _: 0)
@MonkeyClass(hooks, 'after_hostdev_list_by_caps', lambda json: json)
@MonkeyClass(
    hostdev, '_get_udev_block_mapping', lambda: hostdevlib.UDEV_BLOCK_MAP
)
class HostdevTests(TestCaseBase):

    def testProcessDeviceParams(self):
        deviceXML = hostdev._process_device_params(
            libvirtconnection.get()
            .nodeDeviceLookupByName(hostdevlib.ADDITIONAL_DEVICE)
            .XMLDesc()
        )

        self.assertEqual(hostdevlib.ADDITIONAL_DEVICE_PROCESSED, deviceXML)

    def testProcessSRIOV_PFDeviceParams(self):
        deviceXML = hostdev._process_device_params(
            libvirtconnection.get()
            .nodeDeviceLookupByName(hostdevlib.SRIOV_PF)
            .XMLDesc()
        )

        self.assertEqual(hostdevlib.SRIOV_PF_PROCESSED, deviceXML)

    def testProcessSRIOV_VFDeviceParams(self):
        deviceXML = hostdev._process_device_params(
            libvirtconnection.get()
            .nodeDeviceLookupByName(hostdevlib.SRIOV_VF)
            .XMLDesc()
        )

        self.assertEqual(hostdevlib.SRIOV_VF_PROCESSED, deviceXML)

    def testProcessNetDeviceParams(self):
        deviceXML = hostdev._process_device_params(
            libvirtconnection.get()
            .nodeDeviceLookupByName(hostdevlib.NET_DEVICE)
            .XMLDesc()
        )

        self.assertEqual(hostdevlib.NET_DEVICE_PROCESSED, deviceXML)

    def testProcessMdevDeviceParams(self):
        deviceXML = hostdev._process_device_params(
            libvirtconnection.get()
            .nodeDeviceLookupByName(hostdevlib.MDEV_DEVICE)
            .XMLDesc()
        )

        self.assertEqual(hostdevlib.MDEV_DEVICE_PROCESSED, deviceXML)

    def testGetDevicesFromLibvirt(self):
        libvirt_devices, _ = hostdev._get_devices_from_libvirt()

        self.assertEqual(hostdevlib.DEVICES_PROCESSED, libvirt_devices)
        self.assertEqual(
            len(libvirt_devices),
            len(hostdevlib.PCI_DEVICES)
            + len(hostdevlib.USB_DEVICES)
            + len(hostdevlib.SCSI_DEVICES),
        )

    @permutations(
        [[''], [('pci',)], [('usb_device',)], [('pci', 'usb_device')]]
    )
    def testListByCaps(self, caps):
        devices = hostdev.list_by_caps(caps)

        for cap in caps:
            self.assertTrue(
                set(hostdevlib.DEVICES_BY_CAPS[cap].keys()).issubset(
                    set(devices.keys())
                )
            )

    @permutations(
        [
            # addr_type, addr, name
            ('usb', {'bus': '1', 'device': '2'}, 'usb_1_1'),
            ('usb', {'bus': '1', 'device': '10'}, 'usb_1_1_4'),
            (
                'pci',
                {'slot': '26', 'bus': '0', 'domain': '0', 'function': '0'},
                'pci_0000_00_1a_0',
            ),
            (
                'scsi',
                {'bus': '0', 'host': '1', 'lun': '0', 'target': '0'},
                'scsi_1_0_0_0',
            ),
        ]
    )
    def test_device_name_from_address(self, addr_type, addr, name):
        # we need to make sure we scan all the devices (hence caps=None)
        hostdev.list_by_caps()
        self.assertEqual(
            hostdev.device_name_from_address(addr_type, addr), name
        )


@MonkeyClass(libvirtconnection, 'get', hostdevlib.Connection.get)
@MonkeyClass(hostdev, '_sriov_totalvfs', hostdevlib.fake_totalvfs)
@MonkeyClass(hostdev, '_pci_header_type', lambda _: 0)
@MonkeyClass(hooks, 'after_hostdev_list_by_caps', lambda json: json)
class HostdevPerformanceTests(TestCaseBase):

    def test_3k_storage_devices(self):
        with hostdevlib.Connection.use_hostdev_tree():
            self.assertEqual(
                len(hostdev.list_by_caps()),
                len(libvirtconnection.get().listAllDevices()),
            )


@expandPermutations
@MonkeyClass(libvirtconnection, 'get', hostdevlib.Connection)
@MonkeyClass(hostdev, '_sriov_totalvfs', hostdevlib.fake_totalvfs)
@MonkeyClass(hostdev, '_pci_header_type', lambda _: 0)
class HostdevCreationTests(XMLTestCase):

    _PCI_ADDRESS = {
        'slot': '0x02',
        'bus': '0x01',
        'domain': '0x0000',
        'function': '0x0',
        'type': 'pci',
    }

    _PCI_ADDRESS_XML = '<address bus="0x01" domain="0x0000" function="0x0" \
        slot="0x02" type="pci"/>'

    def setUp(self):
        self.conf = {
            'vmName': 'testVm',
            'vmId': '9ffe28b6-6134-4b1e-8804-1185f49c436f',
            'smp': '8',
            'maxVCpus': '160',
            'memSize': '1024',
            'memGuaranteedSize': '512',
        }

    # TODO: next 2 tests should reside in their own module (interfaceTests.py)
    def testCreateSRIOVVF(self):
        dev_spec = {
            'type': hwclass.NIC,
            'device': 'hostdev',
            'hostdev': hostdevlib.SRIOV_VF,
            'macAddr': 'ff:ff:ff:ff:ff:ff',
            'specParams': {'vlanid': 3},
            'bootOrder': '9',
        }
        device = network.Interface(self.log, **dev_spec)
        self.assertXMLEqual(
            xmlutils.tostring(device.getXML()),
            hostdevlib.DEVICE_XML[hostdevlib.SRIOV_VF] % ('',),
        )

    def testCreateSRIOVVFWithAddress(self):
        dev_spec = {
            'type': hwclass.NIC,
            'device': 'hostdev',
            'hostdev': hostdevlib.SRIOV_VF,
            'macAddr': 'ff:ff:ff:ff:ff:ff',
            'specParams': {'vlanid': 3},
            'bootOrder': '9',
            'address': {
                'slot': '0x02',
                'bus': '0x01',
                'domain': '0x0000',
                'function': '0x0',
                'type': 'pci',
            },
        }
        device = network.Interface(self.log, **dev_spec)
        self.assertXMLEqual(
            xmlutils.tostring(device.getXML()),
            hostdevlib.DEVICE_XML[hostdevlib.SRIOV_VF]
            % (self._PCI_ADDRESS_XML),
        )


@expandPermutations
@MonkeyClass(hostdev, '_each_supported_mdev_type', hostdevlib.fake_mdev_types)
@MonkeyClass(hostdev, '_mdev_type_details', hostdevlib.fake_mdev_details)
@MonkeyClass(hostdev, '_mdev_device_vendor', hostdevlib.fake_mdev_vendor)
@MonkeyClass(hostdev, '_mdev_type_devices', hostdevlib.fake_mdev_instances)
@MonkeyClass(hostdev, 'supervdsm', hostdevlib.FakeSuperVdsm())
class TestMdev(TestCaseBase):
    def setUp(self):
        def make_device(name):
            mdev_types = [
                hostdevlib.FakeMdevType('incompatible-1', 2),
                hostdevlib.FakeMdevType('8q', 1),
                hostdevlib.FakeMdevType('4q', 2),
                hostdevlib.FakeMdevType('incompatible-2', 2),
            ]
            return hostdevlib.FakeMdevDevice(
                name=name, vendor='0x10de', mdev_types=mdev_types
            )

        self.devices = [
            make_device(name)
            for name in (
                'card-1',
                'card-2',
            )
        ]

    @permutations(
        [
            # (mdev_type, mdev_uuid)*, mdev_placement, instances
            [[('4q', '4q-1')], hostdev.MdevPlacement.COMPACT, [['4q-1'], []]],
            [[('8q', '8q-1')], hostdev.MdevPlacement.SEPARATE, [['8q-1'], []]],
            [
                [('4q', '4q-1'), ('4q', '4q-2')],
                hostdev.MdevPlacement.COMPACT,
                [['4q-1', '4q-2'], []],
            ],
            [
                [('4q', '4q-1'), ('8q', '8q-1')],
                hostdev.MdevPlacement.COMPACT,
                [['4q-1'], ['8q-1']],
            ],
            [
                [('4q', '4q-1'), ('4q', '4q-2')],
                hostdev.MdevPlacement.SEPARATE,
                [['4q-1'], ['4q-2']],
            ],
            [
                [('4q', '4q-1'), ('8q', '8q-1'), ('4q', '4q-2')],
                hostdev.MdevPlacement.COMPACT,
                [['4q-1', '4q-2'], ['8q-1']],
            ],
            [
                [('8q', '8q-1'), ('4q', '4q-1'), ('4q', '4q-2')],
                hostdev.MdevPlacement.COMPACT,
                [['8q-1'], ['4q-1', '4q-2']],
            ],
            [
                [('4q', '4q-1'), ('4q', '4q-2'), ('8q', '8q-1')],
                hostdev.MdevPlacement.COMPACT,
                [['4q-1', '4q-2'], ['8q-1']],
            ],
            [
                [('4q', '4q-1'), ('8q', '8q-1'), ('4q', '4q-2')],
                hostdev.MdevPlacement.SEPARATE,
                [['4q-1', '4q-2'], ['8q-1']],
            ],
        ]
    )
    def test_vgpu_placement(self, mdev_specs, mdev_placement, instances):
        with MonkeyPatchScope(
            [(hostdev, '_each_mdev_device', lambda: self.devices)]
        ):
            for mdev_type, mdev_uuid in mdev_specs:
                mdev_properties = hostdev.MdevProperties(
                    mdev_type, mdev_placement, None
                )
                hostdev.spawn_mdev(mdev_properties, mdev_uuid, self.log)
        for inst, dev in zip(instances, self.devices):
            dev_inst = []
            for mdev_type in dev.mdev_types:
                dev_inst.extend(mdev_type.instances)
            self.assertEqual(inst, dev_inst)

    @permutations(
        [
            [hostdev.MdevPlacement.COMPACT],
            [hostdev.MdevPlacement.SEPARATE],
        ]
    )
    def test_unsupported_vgpu_placement(self, placement):
        with MonkeyPatchScope(
            [(hostdev, '_each_mdev_device', lambda: self.devices)]
        ):
            mdev_properties = hostdev.MdevProperties(
                'unsupported', placement, None
            )
            self.assertRaises(
                exception.ResourceUnavailable,
                hostdev.spawn_mdev,
                mdev_properties,
                '1234',
                self.log,
            )


# NVIDIA vendor-specific VFIO framework: two GPUs with two VFs each.
_NVIDIA_GPUS = {
    '0000:41:00.0': ('0000:41:00.4', '0000:41:00.5'),
    '0000:81:00.0': ('0000:81:00.4', '0000:81:00.5'),
}
_NVIDIA_CREATABLE = '''\
ID    : vGPU Name
590   : NVIDIA A10-1Q
593   : NVIDIA A10-4Q
'''


class FakeNvidiaSuperVdsm:

    def __init__(self, sysfs):
        self.sysfs = sysfs

    def getProxy(self):
        return self

    def nvidia_vgpu_create(self, vf, type_id, driver_parameters):
        (self.sysfs / vf / 'nvidia' / 'current_vgpu_type').write_text(
            '%d\n' % type_id
        )

    def nvidia_vgpu_delete(self, vf):
        (self.sysfs / vf / 'nvidia' / 'current_vgpu_type').write_text('0\n')


@pytest.fixture
def nvidia_sysfs(tmp_path, monkeypatch):
    for pf, vfs in _NVIDIA_GPUS.items():
        (tmp_path / pf).mkdir()
        for vf in vfs:
            nvidia = tmp_path / vf / 'nvidia'
            nvidia.mkdir(parents=True)
            (nvidia / 'creatable_vgpu_types').write_text(_NVIDIA_CREATABLE)
            (nvidia / 'current_vgpu_type').write_text('0\n')
            (tmp_path / vf / 'physfn').symlink_to(tmp_path / pf)
    monkeypatch.setattr(hostdev, '_PCI_DEVICES_PATH', str(tmp_path))
    monkeypatch.setattr(hostdev, '_MDEV_PATH', str(tmp_path / 'no-mdev'))
    monkeypatch.setattr(hostdev, 'supervdsm', FakeNvidiaSuperVdsm(tmp_path))
    monkeypatch.setattr(hostdev, '_nvidia_vgpus', {})
    return tmp_path


def _current_vgpu_types(sysfs):
    return {
        vf: int((sysfs / vf / 'nvidia' / 'current_vgpu_type').read_text())
        for vfs in _NVIDIA_GPUS.values()
        for vf in vfs
    }


@pytest.mark.parametrize(
    'content',
    [
        _NVIDIA_CREATABLE,
        # The driver prints the names as they come; don't rely on newlines.
        'ID    : vGPU Name\n590   : NVIDIA A10-1Q593   : NVIDIA A10-4Q',
    ],
)
def test_nvidia_creatable_vgpu_types(nvidia_sysfs, content):
    (
        nvidia_sysfs / '0000:41:00.4' / 'nvidia' / 'creatable_vgpu_types'
    ).write_text(content)
    assert hostdev._nvidia_creatable_vgpu_types('0000:41:00.4') == {
        590: 'NVIDIA A10-1Q',
        593: 'NVIDIA A10-4Q',
    }


def test_nvidia_vgpu_mdev_types(nvidia_sysfs):
    (
        nvidia_sysfs / '0000:41:00.5' / 'nvidia' / 'current_vgpu_type'
    ).write_text('593\n')
    assert hostdev._nvidia_vgpu_mdev_types('0000:41:00.4') == {
        'nvidia-590': {'name': 'NVIDIA A10-1Q', 'available_instances': '1'},
        'nvidia-593': {'name': 'NVIDIA A10-4Q', 'available_instances': '1'},
    }
    assert hostdev._nvidia_vgpu_mdev_types('0000:41:00.5') == {
        'nvidia-590': {'name': 'NVIDIA A10-1Q', 'available_instances': '0'},
        'nvidia-593': {'name': 'NVIDIA A10-4Q', 'available_instances': '0'},
    }
    assert hostdev._nvidia_vgpu_mdev_types('0000:41:00.0') == {}


@pytest.mark.parametrize(
    'placement, expected',
    [
        (
            hostdev.MdevPlacement.COMPACT,
            {
                '0000:41:00.4': 593,
                '0000:41:00.5': 593,
                '0000:81:00.4': 0,
                '0000:81:00.5': 0,
            },
        ),
        (
            hostdev.MdevPlacement.SEPARATE,
            {
                '0000:41:00.4': 593,
                '0000:41:00.5': 0,
                '0000:81:00.4': 593,
                '0000:81:00.5': 0,
            },
        ),
    ],
)
def test_nvidia_vgpu_placement(nvidia_sysfs, placement, expected):
    log = logging.getLogger('test')
    for mdev_uuid in ('uuid-1', 'uuid-2'):
        mdev_properties = hostdev.MdevProperties('nvidia-593', placement, None)
        hostdev.spawn_mdev(mdev_properties, mdev_uuid, log)
    assert _current_vgpu_types(nvidia_sysfs) == expected
    vfs = [vf for vf, type_id in expected.items() if type_id]
    assert [hostdev.nvidia_vgpu_vf(u) for u in ('uuid-1', 'uuid-2')] == vfs


@pytest.mark.parametrize('mdev_type', ['nvidia-999', 'nvidia-', 'other-593'])
def test_nvidia_vgpu_unavailable_type(nvidia_sysfs, mdev_type):
    mdev_properties = hostdev.MdevProperties(
        mdev_type, hostdev.MdevPlacement.COMPACT, None
    )
    with pytest.raises(exception.ResourceUnavailable):
        hostdev.spawn_mdev(mdev_properties, 'uuid-1', logging.getLogger())


def test_nvidia_vgpu_no_free_vf(nvidia_sysfs):
    log = logging.getLogger('test')
    mdev_properties = hostdev.MdevProperties(
        'nvidia-590', hostdev.MdevPlacement.COMPACT, None
    )
    for i in range(4):
        hostdev.spawn_mdev(mdev_properties, 'uuid-%d' % i, log)
    with pytest.raises(exception.ResourceUnavailable):
        hostdev.spawn_mdev(mdev_properties, 'uuid-4', log)


def test_nvidia_vgpu_despawn(nvidia_sysfs):
    mdev_properties = hostdev.MdevProperties(
        'nvidia-593', hostdev.MdevPlacement.COMPACT, None
    )
    hostdev.spawn_mdev(mdev_properties, 'uuid-1', logging.getLogger())
    hostdev.despawn_mdev('uuid-1')
    assert not any(_current_vgpu_types(nvidia_sysfs).values())
    assert hostdev.nvidia_vgpu_vf('uuid-1') is None


def test_nvidia_vgpu_release_by_vf(nvidia_sysfs):
    # VM teardown knows only the VF from the running domain XML, and vdsm may
    # have restarted since the vGPU was created.
    mdev_properties = hostdev.MdevProperties(
        'nvidia-593', hostdev.MdevPlacement.COMPACT, None
    )
    hostdev.spawn_mdev(mdev_properties, 'uuid-1', logging.getLogger())
    hostdev._nvidia_vgpus.clear()
    assert hostdev.is_nvidia_vgpu_vf('0000:41:00.4')
    hostdev.release_nvidia_vgpu('0000:41:00.4')
    assert not any(_current_vgpu_types(nvidia_sysfs).values())


def test_nvidia_vgpu_vf_teardown(nvidia_sysfs, monkeypatch):
    from vdsm.virt.vmdevices import hostdevice

    def reattach(*args, **kwargs):
        raise AssertionError('vGPU VF must not be reattached')

    mdev_properties = hostdev.MdevProperties(
        'nvidia-593', hostdev.MdevPlacement.COMPACT, None
    )
    hostdev.spawn_mdev(mdev_properties, 'uuid-1', logging.getLogger())
    monkeypatch.setattr(hostdevice, 'reattach_detachable', reattach)
    monkeypatch.setattr(
        hostdevice,
        '_get_device_name_type',
        lambda dom: ('pci_0000_41_00_4', 'pci'),
    )
    hostdevice.teardown_device(None, logging.getLogger())
    assert not any(_current_vgpu_types(nvidia_sysfs).values())
