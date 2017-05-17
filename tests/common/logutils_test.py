#
# Copyright 2016-2017 Red Hat, Inc.
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

import logging

from testlib import VdsmTestCase as TestCaseBase
from testlib import forked

from vdsm.common import logutils


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
        data = logutils.AllVmStatsValue(self._STATS)
        result = str(data)
        self.assertEqual(eval(result), self._SIMPLIFIED)


class TestSetLevel(TestCaseBase):

    @forked
    def test_root_logger(self):
        logger = logging.getLogger()
        logutils.set_level("WARNING")
        self.assertEqual(logger.getEffectiveLevel(), logging.WARNING)

    @forked
    def test_other_logger(self):
        name = "test"
        logger = logging.getLogger(name)
        logutils.set_level("WARNING", name=name)
        self.assertEqual(logger.getEffectiveLevel(), logging.WARNING)

    @forked
    def test_sub_logger(self):
        name = "test.sublogger"
        logger = logging.getLogger(name)
        logutils.set_level("WARNING", name=name)
        self.assertEqual(logger.getEffectiveLevel(), logging.WARNING)

    @forked
    def test_non_existing_level(self):
        with self.assertRaises(ValueError):
            logutils.set_level("NO SUCH LEVEL")

    @forked
    def test_level_alias(self):
        logging.addLevelName("OOPS", logging.ERROR)
        logger = logging.getLogger()

        # The new alias should work...
        logutils.set_level("OOPS")
        self.assertEqual(logger.getEffectiveLevel(), logging.ERROR)

        # The old name should work as well.
        logutils.set_level("ERROR")
        self.assertEqual(logger.getEffectiveLevel(), logging.ERROR)
