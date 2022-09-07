# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

from vdsm.common import xmlutils
from vdsm.virt.vmdevices import common
from vdsm.virt.vmdevices import hwclass
from vdsm.virt.vmdevices import lookup

from testlib import VdsmTestCase
from testlib import expandPermutations, permutations
import pytest


_DRIVES_XML = [
    # drive_xml, dev_name - if None, we expect LookupError, alias
    (u'''<disk device="disk" snapshot="no" type="file" />''', None, None),
    (u'''<disk device="disk" snapshot="no" type="file">
          <serial>virtio0000</serial>
        </disk>''', 'vdb', None),
    # TODO: check it is valid for user aliases too
    (u'''<disk device="disk" snapshot="no" type="file">
          <alias name='ua-0000' />
        </disk>''', 'sda', 'ua-0000'),
    (u'''<disk device="disk" snapshot="no" type="file">
          <serial>virtio1111</serial>
          <alias name='ua-0000' />
        </disk>''', 'sda', 'ua-0000'),
]


@expandPermutations
class TestLookup(VdsmTestCase):

    def setUp(self):
        self.drives = [
            FakeDrive(
                name='sda',
                serial='scsi0000',
                alias='ua-0000'
            ),
            FakeDrive(
                name='vdb',
                serial='virtio0000',
                alias='ua-2001'
            ),
        ]
        self.devices_conf = [
            {'alias': 'dimm0', 'type': 'memory', 'size': 1024},
            {'alias': 'ac97', 'type': 'sound'}
        ]
        self.devices = common.empty_dev_map()
        self.device_xml = xmlutils.fromstring("""
            <devices>
              <disk><alias name='ua-1'/></disk>
              <hostdev><alias name='ua-2'/></hostdev>
              <interface><alias name='ua-3'/></interface>
            </devices>
        """)

    def test_lookup_drive_by_name_found(self):
        drive = lookup.drive_by_name(self.drives, 'sda')
        assert drive is self.drives[0]

    def test_lookup_drive_by_name_missing(self):
        with pytest.raises(LookupError):
            lookup.drive_by_name(self.drives, 'hdd')

    def test_lookup_drive_by_serial_found(self):
        drive = lookup.drive_by_serial(self.drives, 'scsi0000')
        assert drive is self.drives[0]

    def test_lookup_drive_by_serial_missing(self):
        with pytest.raises(LookupError):
            lookup.drive_by_serial(self.drives, 'ide0002')

    def test_lookup_device_by_alias_found(self):
        device = lookup.device_by_alias(self.drives, 'ua-0000')
        assert device is self.drives[0]

    def test_lookup_device_by_alias_missing(self):
        with pytest.raises(LookupError):
            lookup.device_by_alias(self.drives, 'ua-UNKNOWN')

    def test_lookup_xml_device_by_alias_found(self):
        device = lookup.xml_device_by_alias(self.device_xml, 'ua-2')
        assert device.tag == 'hostdev'

    def test_lookup_xml_device_by_alias_missing(self):
        with pytest.raises(LookupError):
            lookup.xml_device_by_alias(self.device_xml, 'ua-UNKNOWN')

    @permutations([[c] for c in hwclass.HOTPLUGGABLE])
    def test_hotpluggable_device_by_alias_found(self, device_class):
        self.devices[device_class] = self.drives
        device = lookup.hotpluggable_device_by_alias(self.devices, 'ua-0000')
        assert device[0] is self.drives[0]
        assert device[1] == device_class

    def test_hotpluggable_device_by_alias_missing(self):
        with pytest.raises(LookupError):
            lookup.hotpluggable_device_by_alias(self.devices, 'ua-UNKNOWN')

    @permutations(_DRIVES_XML)
    def test_lookup_drive_by_element(self, drive_xml, dev_name, alias_name):
        # intentionally without serial and alias
        if dev_name is None:
            with pytest.raises(LookupError):
                lookup.drive_from_element(
                    self.drives, xmlutils.fromstring(drive_xml)
                )
        else:
            drive = lookup.drive_from_element(
                self.drives,
                xmlutils.fromstring(drive_xml)
            )
            assert drive.name == dev_name

    @permutations(_DRIVES_XML)
    def test_lookup_device_from_xml_alias(
            self, drive_xml, dev_name, alias_name):
        # intentionally without serial and alias
        if dev_name is None or alias_name is None:
            with pytest.raises(LookupError):
                lookup.device_from_xml_alias(
                    self.drives, drive_xml
                )
        else:
            drive = lookup.device_from_xml_alias(
                self.drives,
                drive_xml
            )
            assert drive.name == dev_name

    @permutations([
        ['memory', 'dimm0', 0],
        ['sound', 'ac97', 1],
    ])
    def test_lookup_conf(self, dev_type, alias, index):
        conf = lookup.conf_by_alias(
            self.devices_conf, dev_type, alias)
        assert conf == self.devices_conf[index]

    @permutations([
        ['memory', 'dimm1'],
        ['sound', 'dimm0'],
    ])
    def test_lookup_conf_error(self, dev_type, alias):
        with pytest.raises(LookupError):
            lookup.conf_by_alias(self.devices_conf, dev_type, alias)

    @permutations([
        # devices_conf
        [[]],
        [[{}]],
    ])
    def test_lookup_conf_missing(self, devices_conf):
        with pytest.raises(LookupError):
            lookup.conf_by_alias(devices_conf, 'memory', 'dimm0')

    @permutations([
        # devices_conf
        [[]],
        [[{}]],
        [[{'alias': 'ac97', 'type': 'sound'}]],
    ])
    def test_lookup_conf_by_path_missing(self, devices_conf):
        with pytest.raises(LookupError):
            lookup.conf_by_path(devices_conf, '/fake/test/path')

    @permutations([
        # devices_conf, path, dev_index
        [
            [{'path': '/foo/bar', 'type': hwclass.DISK}],
            '/foo/bar',
            0
        ],
    ])
    def test_lookup_conf_by_path(self, devices_conf, path, dev_index):
        drive = lookup.conf_by_path(devices_conf, path)
        assert drive == devices_conf[dev_index]


class FakeDrive(object):
    def __init__(self, name='vda', serial='0000', alias='ua-0'):
        self.name = name
        self.serial = serial
        self.alias = alias
