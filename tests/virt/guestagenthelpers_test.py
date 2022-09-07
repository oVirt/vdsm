# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

from vdsm.virt import guestagenthelpers

from testlib import VdsmTestCase as TestCaseBase


class GuestAgentHelpersTest(TestCaseBase):

    def test_translate_arch(self):
        assert 'x86_64' == guestagenthelpers.translate_arch('x86_64')
        assert 'x86' == guestagenthelpers.translate_arch('x86')
        assert 'x86' == guestagenthelpers.translate_arch('i386')
        # Something not in the map
        assert 'unknown' == guestagenthelpers.translate_arch('ia64')

    def test_translate_linux_osinfo(self):
        assert guestagenthelpers.translate_linux_osinfo({}) == \
            {
                'guestOs': '',
                'guestOsInfo': {
                    'type': 'linux',
                    'arch': 'unknown',
                    'kernel': '',
                    'distribution': '',
                    'version': '',
                    'codename': '',
                },
        }
        assert guestagenthelpers.translate_linux_osinfo({
            "id": "some-id",
            "kernel-release": "some-release",
            "kernel-version": "some-version",
            "machine": "x86_64",
            "name": "some-name",
            "pretty-name": "pretty name",
            "variant": "my variant",
            "variant-id": "some-variant",
            "version": "123 my version",
            "version-id": "123",
        }) == {
            'guestOs': 'some-release',
            'guestOsInfo': {
                'type': 'linux',
                'arch': 'x86_64',
                'kernel': 'some-release',
                'distribution': 'some-name',
                'version': '123',
                'codename': 'my variant',
            },
        }

    def test_translate_windows_osinfo(self):
        assert guestagenthelpers.translate_windows_osinfo({}) == \
            {
                'guestOs': '',
                'guestOsInfo': {
                    'type': 'windows',
                    'arch': 'unknown',
                    'kernel': '',
                    'distribution': '',
                    'version': '',
                    'codename': '',
                },
        }
        assert guestagenthelpers.translate_windows_osinfo({
            "id": "some-id",
            "kernel-release": "1234",
            "kernel-version": "6.1",
            "machine": "x86_64",
            "name": "some-name",
            "pretty-name": "Windows 7 Standard",
            "variant": "client",
            "variant-id": "client",
            "version": "10",
            "version-id": "10",
        }) == {
            'guestOs': 'Windows 7 Standard',
            'guestOsInfo': {
                'type': 'windows',
                'arch': 'x86_64',
                'kernel': '',
                'distribution': '',
                'version': '6.1',
                'codename': 'Windows 7 Standard',
            },
        }

    def test_translate_fsinfo(self):
        assert guestagenthelpers.translate_fsinfo({
            'name': 'dm-3',
            'used-bytes': 123,
            'total-bytes': 456,
            'mountpoint': '/home',
            'disk': [],
            'type': 'ext4',
        }) == {
            'fs': 'ext4',
            'path': '/home',
            'total': '456',
            'used': '123',
        }

    def test_translate_pci_device_iso_date(self):
        assert guestagenthelpers.translate_pci_device({
            'driver-date': '2019-08-12',
            'driver-name': 'Red Hat VirtIO Ethernet Adapter',
            'driver-version': '100.80.104.17300',
            'address': {
                'type': 'pci',
                'data': {
                    'device-id': 4096,
                    'vendor-id': 6900,
                }
            }
        }) == {
            'device_id': 4096,
            'driver_date': '2019-08-12',
            'driver_name': 'Red Hat VirtIO Ethernet Adapter',
            'driver_version': '100.80.104.17300',
            'vendor_id': 6900,
        }

    def test_translate_pci_device_timestamp(self):
        assert guestagenthelpers.translate_pci_device({
            'driver-date': 1565568000000000000,
            'driver-name': 'Red Hat VirtIO Ethernet Adapter',
            'driver-version': '100.80.104.17300',
            'id': {
                'type': 'pci',
                'device-id': 4096,
                'vendor-id': 6900,
            }
        }) == {
            'device_id': 4096,
            'driver_date': '2019-08-12',
            'driver_name': 'Red Hat VirtIO Ethernet Adapter',
            'driver_version': '100.80.104.17300',
            'vendor_id': 6900,
        }
