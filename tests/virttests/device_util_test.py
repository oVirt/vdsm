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
from vdsm.virt.vmdevices import hwclass

from testlib import make_config
from testlib import VdsmTestCase
from testlib import expandPermutations, permutations
from monkeypatch import MonkeyPatchScope


@expandPermutations
class VMDevicesCommonDriveIdentAttrTests(VdsmTestCase):

    @permutations([
        # whitelist, expected
        ['', set()],
        ['controller', set()],
        ['RNG,console', set()],
        ['ALL', set(hwclass.TO_REFRESH)],
        [','.join(hwclass.TO_REFRESH), set(hwclass.TO_REFRESH)],
        ['graphics', set(('graphics',))],
        [' lease,  graphics', set(('graphics', 'lease'))],
        ['LEASE', set(('lease',))],
        ['controller,lease,graphics', set(('graphics', 'lease'))],
    ])
    def test_get_refreshable_device_classes(self, whitelist, expected):
        with MonkeyPatchScope([
            (common, 'config',
             make_config([('devel', 'device_xml_refresh_enable', whitelist)]))
        ]):
            self.assertEqual(
                common.get_refreshable_device_classes(),
                expected
            )
