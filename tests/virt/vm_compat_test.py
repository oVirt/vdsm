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

from vdsm.virt.vmdevices import hwclass
from vdsm.virt import libvirtxml

from testlib import VdsmTestCase
from testlib import read_data


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


class ParseDomainXMLTests(VdsmTestCase):

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
