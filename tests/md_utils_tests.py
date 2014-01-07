#
# Copyright 2012 Red Hat, Inc.
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

from testrunner import VdsmTestCase as TestCaseBase
import md_utils as mdutils


class MdUtilsTests(TestCaseBase):
    def test_parseMdDeviceMap(self):
        lines = ['md1 1.2 12345695:0b784539:a73e011c:c555adf3 /dev/md/1',
                 'md0 1.2 123456e3:a89f1a62:e40e6a27:5f6bbca1 /dev/md/0',
                 'md2 1.2 1234566d:9f136504:efb8e4be:12810206 /dev/md2']

        devUuidMap = mdutils._parseMdDeviceMap(lines)
        self.assertTrue(devUuidMap)
        for dev, uuid in devUuidMap.iteritems():
            if dev in ['/dev/md1', '/dev/md/1']:
                self.assertEquals(uuid, '12345695:0b784539:a73e011c:c555adf3')
            elif dev in ['/dev/md0', '/dev/md/0']:
                self.assertEquals(uuid, '123456e3:a89f1a62:e40e6a27:5f6bbca1')
            elif dev == '/dev/md2':
                self.assertEquals(uuid, '1234566d:9f136504:efb8e4be:12810206')
            else:
                self.assertIn(dev, ['/dev/md1', '/dev/md/1', '/dev/md0',
                                    '/dev/md/0', '/dev/md2'])
