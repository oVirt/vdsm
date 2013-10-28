#
# Copyright 2013 Red Hat, Inc.
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
from vdsm.vdscli import TransportWrapper


class FakeTransport:
    def parse_response(self, response):
        return response


class TransportTest(TestCaseBase):
    def testTransportWrapper(self):
        transport = FakeTransport()
        TransportWrapper(transport)
        response = 'My response'
        self.assertEquals(response, transport.parse_response(response))
