#
# Copyright 2016 Red Hat, Inc.
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

from vdsm.logUtils import AllVmStatsValue

from testlib import VdsmTestCase as TestCaseBase


class TestAllVmStats(TestCaseBase):

    _STATS = [{'foo': 'bar',
               'status': 'Up',
               'vmId': u'43f02a2d-e563-4f11-a7bc-9ee191cfeba1'},
              {'foo': 'bar',
               'status': 'Powering up',
               'vmId': u'bd0d066b-971e-42f8-8bc6-d647ab7e0e70'}]
    _SIMPLIFIED = ({u'43f02a2d-e563-4f11-a7bc-9ee191cfeba1': 'Up',
                    u'bd0d066b-971e-42f8-8bc6-d647ab7e0e70': 'Powering up'})

    def test_allvmstats(self):
        data = AllVmStatsValue(self._STATS)
        result = str(data)
        self.assertEqual(eval(result), self._SIMPLIFIED)
