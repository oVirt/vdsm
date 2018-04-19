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
from __future__ import division

from vdsm.common import cpuarch
from vdsm.common import libvirtconnection
from vdsm import constants
from vdsm import host
from vdsm import osinfo

from vdsm.common.compat import json
from vdsm.virt.vmdevices import hwclass
from vdsm.virt import libvirtxml
from vdsm.virt import recovery
from vdsm.virt import vm

from monkeypatch import MonkeyPatch

from testlib import VdsmTestCase
from testlib import read_data

import vmfakelib as fake


# Engine doesn't care about those:
IGNORED_DEVICE_TYPES = (
    'channel',
)

# We have most of the vm.conf keys handy, so it is easier
# to check everything but the few keys we don't handle,
# and Engine doesn't care about.
VM_KEYS_BLACKLIST = (
    # those are updated automatically by vm.status()
    'status', 'statusTime', 'guestDiskMapping',
    # unused in Vdsm >= 3.6
    'pitReinjection', 'smartcardEnable', 'transparentHugePages',
    # Engine ignores these when reading the response from Vdsm
    'displayNetwork', 'nice',
    # Always added by Vdsm >= 4.3
    'clientIp',
    # tested separately
    'devices',
)

# Looking at VMS monitoring code in Engine, we see it doesn't
# care about most of device data. This data is mostly used by
# Vdsm in the recovering flow.
# Here we check only the fields we know Engine cares about.
# This way we have simpler and more robust tests.
DEVICE_KEYS_WHITELIST = (
    'deviceId', 'alias', 'address', 'hostdev'
)


class VMConfFromXMLTests(VdsmTestCase):

    @MonkeyPatch(cpuarch, 'effective', lambda: cpuarch.X86_64)
    @MonkeyPatch(osinfo, 'version', lambda: {
        'release': '1', 'version': '18', 'name': 'Fedora'})
    @MonkeyPatch(constants, 'SMBIOS_MANUFACTURER', 'oVirt')
    @MonkeyPatch(constants, 'SMBIOS_OSNAME', 'oVirt Node')
    @MonkeyPatch(libvirtconnection, 'get', fake.Connection)
    @MonkeyPatch(host, 'uuid',
                 lambda: "fc25cbbe-5520-4f83-b82e-1541914753d9")
    @MonkeyPatch(vm.Vm, 'send_status_event', lambda x: None)
    def test_compat41(self):
        expected_conf = json.loads(
            read_data('vm_compat41.json'))[0]

        vm_params = recovery._recovery_params(
            expected_conf['vmId'],
            read_data('vm_compat41.xml'),
            False)

        vm_obj = vm.Vm(fake.ClientIF(), vm_params, recover=True)
        # TODO: ugly hack, but we don't have APIs to do that
        vm_obj._devices = vm_obj._make_devices()

        recovered_conf = vm_obj.status(fullStatus=True)

        self.assert_conf_equal(
            recovered_conf, expected_conf, filter_vm_conf_keys)

        self.assert_devices_conf_equal(
            recovered_conf['devices'], expected_conf['devices'],
            IGNORED_DEVICE_TYPES)

    def assert_devices_conf_equal(self, actual_devs, expected_devs,
                                  device_classes_to_ignore):

        for expected_dev_conf in expected_devs:
            if expected_dev_conf['type'] in device_classes_to_ignore:
                continue

            attrs = find_match_attrs(expected_dev_conf)

            actual_dev_conf = find_dev_conf_by_attrs(actual_devs, **attrs)
            self.assertIsNotNone(actual_dev_conf)

            self.assert_conf_equal(
                actual_dev_conf, expected_dev_conf, filter_dev_conf_keys)

    def assert_conf_equal(self, actual, expected, filter_keys):
        expected_keys = filter_keys(expected)
        for key in sorted(expected_keys):
            self.assertEqual(
                actual[key], expected[key],
                "comparing key %s: actual=%s expected=%s" % (
                    key, actual[key], expected[key]))


class ParseDomainXMLTests(VdsmTestCase):

    def test_vm_compat_41(self):
        dom_xml = read_data('vm_compat41.xml')
        conf = libvirtxml.parse_domain(dom_xml, cpuarch.X86_64)
        self.assertEqual(int(conf['smp']), 2)

    def test_hosted_engine_42(self):
        dom_xml = read_data('vm_hosted_engine_42.xml')
        conf = libvirtxml.parse_domain(dom_xml, cpuarch.X86_64)
        self.assertEqual(int(conf['smp']), 2)


def find_match_attrs(dev_conf):
    # see comment in vmdevices.graphics.Graphics.get_identifying_attrs()
    if dev_conf['type'] == hwclass.GRAPHICS:
        return {
            'type': dev_conf['type'],
            'device': dev_conf['device'],
        }
    else:
        return {
            'type': dev_conf['type'],
            'alias': dev_conf['alias'],
        }


def find_dev_conf_by_attrs(dev_confs, **kwargs):
    for dev_conf in dev_confs:
        items = {
            key: dev_conf.get(key, None)
            for key, value in kwargs.items()
            if dev_conf.get(key, None) is not None
        }
        if kwargs == items:
            return dev_conf
    return None


def filter_vm_conf_keys(vm_conf):
    return {
        key for key in vm_conf.keys()
        if key not in VM_KEYS_BLACKLIST
    }


def filter_dev_conf_keys(dev_conf):
    return {
        key for key in dev_conf.keys()
        if key in DEVICE_KEYS_WHITELIST
    }
