#
# Copyright 2018 Red Hat, Inc.
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

from vdsm.common import cpuarch
from vdsm.virt import domxml_preprocess
from vdsm.virt import vmdevices
from vdsm.virt import vmxml

from testlib import VdsmTestCase
from testlib import read_data

import vmfakelib as fake

from monkeypatch import MonkeyPatchScope


class TestFixLease(VdsmTestCase):

    def setUp(self):
        self.cif = fake.ClientIF()
        self.xml_str = read_data('hostedengine_lease.xml')
        self.disk_devs = domxml_preprocess._make_disk_devices(
            self.xml_str, self.log)

        self.driveVolInfo = {
            'leasePath': 'drive-lease-path',
            'leaseOffset': 'drive-lease-offset',
        }
        self.vmVolInfo = {
            'leasePath': 'vm-lease-path',
            'leaseOffset': 'vm-lease-offset',
        }

    def test_drive_lease(self):
        """
        test that we fill the drive lease. Happy path.
        """
        disk_devs = self._inject_volume_chain(
            self.disk_devs, self.driveVolInfo)

        xml_str = domxml_preprocess.replace_placeholders(
            self.xml_str, self.cif, cpuarch.X86_64, '0000',
            {vmdevices.hwclass.DISK: disk_devs})

        self._check_leases(xml_str, [self.driveVolInfo])

    def test_drive_lease_without_volume_chain(self):
        """
        Should we lack volumeChain attribute (like cdroms), this
        should not raise.
        We treat leases like VM lease, because we cannot distinguish
        this case.
        """

        def _fake_lease_info(*args, **kwargs):
            return {
                'result': {
                    'path': self.vmVolInfo['leasePath'],
                    'offset': self.vmVolInfo['leaseOffset'],
                }
            }

        with MonkeyPatchScope([
            (self.cif.irs, 'lease_info', _fake_lease_info),
        ]):
            xml_str = domxml_preprocess.replace_placeholders(
                self.xml_str, self.cif, cpuarch.X86_64, '0000',
                {vmdevices.hwclass.DISK: self.disk_devs})

        self._check_leases(xml_str, [self.vmVolInfo])

    def test_drive_lease_chain_not_matches(self):
        """
        We have no choice but consider this a VM lease.
        """

        disk_devs = self._inject_volume_chain(
            self.disk_devs, self.driveVolInfo,
            domainID='unknwonDomainID',
            volumeID='unknownVolumeID')

        def _fake_lease_info(*args, **kwargs):
            return {
                'result': {
                    'path': self.vmVolInfo['leasePath'],
                    'offset': self.vmVolInfo['leaseOffset'],
                }
            }

        with MonkeyPatchScope([
            (self.cif.irs, 'lease_info', _fake_lease_info),
        ]):
            xml_str = domxml_preprocess.replace_placeholders(
                self.xml_str, self.cif, cpuarch.X86_64, '0000',
                {vmdevices.hwclass.DISK: disk_devs})

        self._check_leases(xml_str, [self.vmVolInfo])

    def _check_leases(self, xml_str, vol_infos):
        xml_dom = vmxml.parse_xml(xml_str)
        lease_elems = xml_dom.findall('./devices/lease')
        self.assertEqual(len(lease_elems), len(vol_infos))

        for lease_elem, vol_info in zip(lease_elems, vol_infos):
            target = vmxml.find_first(lease_elem, 'target')
            self.assertEqual(
                target.attrib['path'], vol_info['leasePath'])
            self.assertEqual(
                target.attrib['offset'], vol_info['leaseOffset'])

    def _inject_volume_chain(self, disk_devs, volInfo,
                             domainID=None, volumeID=None):
        drives = []
        for drive in disk_devs:
            if drive.device != 'disk':
                continue

            # the leases code uses only the leaf node. So let's use
            # obviously bogus nodes which we should ignore
            bogus = {
                'domainID': 'bogus because should be ignored',
                'volumeID': 'bogus because should be ignored',
                'leasePath': 'bogus because should be ignored',
                'leaseOffset': 'bogus because should be ignored',
            }
            # the leaf node is the only one used, so we fill it with
            # meaningful data
            leaf = {
                'domainID': domainID or drive.domainID,
                'volumeID': volumeID or drive.volumeID,
            }
            leaf.update(volInfo)
            drive.volumeChain = [bogus, bogus, leaf]
            drives.append(drive)
            break  # assume only one disk

        return drives
