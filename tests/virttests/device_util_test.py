#
# Copyright 2017 Red Hat, Inc.
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

from vdsm.virt.vmdevices import common

from testlib import VdsmTestCase
from testlib import expandPermutations, permutations


@expandPermutations
class VMDevicesCommonDriveIdentAttrTests(VdsmTestCase):

    @permutations([
        # dev_conf
        [{}],
        [{'device': 'disk'}],  # should use 'type'
        [{'type': 'disk', 'GUID': 'some_guid'}],
        [{'type': 'disk', 'iface': 'scsi'}],  # missing index
        [{'type': 'disk', 'index': '0'}],  # missing interface
    ])
    def test_drive_not_identifiable(self, dev_conf):
        self.assertRaises(
            LookupError,
            common.get_drive_conf_identifying_attrs,
            dev_conf
        )

    @permutations([
        # dev_conf, name
        [{'device': 'disk', 'iface': 'scsi', 'index': 0}, 'sda'],
        [{'device': 'disk', 'name': 'vda'}, 'vda'],
    ])
    def test_drive_identified_by_name(self, dev_conf, name):
        attrs = common.get_drive_conf_identifying_attrs(dev_conf)
        self.assertEqual({'type': 'disk', 'name': name}, attrs)
