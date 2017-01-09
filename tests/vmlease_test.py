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

from __future__ import absolute_import

import six
from nose.plugins.skip import SkipTest

from vdsm.common import exception
from vdsm.common import response

from virt import vmdevices
from virt import vmxml

from testlib import VdsmTestCase
from testlib import XMLTestCase
from testlib import expandPermutations, permutations


@expandPermutations
class TestDevice(XMLTestCase):

    def setUp(self):
        # TODO: replace with @skipif when available
        if six.PY3:
            raise SkipTest("vmdevices not compatible with python 3")

    def test_getxml(self):
        spec = dict(sd_id="sd_id", lease_id="lease_id", path="/path",
                    offset=1048576)
        lease = vmdevices.lease.Device({}, self.log, **spec)
        lease_xml = vmxml.format_xml(lease.getXML())
        xml = """
        <lease>
            <key>lease_id</key>
            <lockspace>sd_id</lockspace>
            <target offset="1048576" path="/path" />
        </lease>
        """
        self.assertXMLEqual(lease_xml, xml)

    @permutations([["sd_id"], ["lease_id"], ["path"], ["offset"]])
    def test_missing_required_argument(self, missing):
        kwargs = {"sd_id": "sd_id",
                  "lease_id": "lease_id",
                  "path": "path",
                  "offset": 0}
        del kwargs[missing]
        with self.assertRaises(vmdevices.lease.MissingArgument):
            vmdevices.lease.Device({}, self.log, **kwargs)

    def test_repr(self):
        kwargs = {"sd_id": "sd_id",
                  "lease_id": "lease_id",
                  "path": "path",
                  "offset": 0}
        lease = vmdevices.lease.Device({}, self.log, **kwargs)
        for key, value in kwargs.items():
            self.assertIn("%s=%s" % (key, value), repr(lease))


@expandPermutations
class TestPrepare(VdsmTestCase):

    def test_unprepared(self):
        storage = FakeStorage()
        key = ("sd_id", "lease_id")
        storage.leases[key] = {"path": "path", "offset": 0}
        device = {"type": "lease",
                  "sd_id": "sd_id",
                  "lease_id": "lease_id"}
        expected = device.copy()
        expected["path"] = "path"
        expected["offset"] = 0
        vmdevices.lease.prepare(storage, [device])
        self.assertEqual(device, expected)

    def test_skip_prepared(self):
        device = {"type": "lease",
                  "sd_id": "sd_id",
                  "lease_id": "lease_id",
                  "path": "path",
                  "offset": 0}
        with self.assertNotRaises():
            vmdevices.lease.prepare(None, [device])

    def test_no_such_lease(self):
        storage = FakeStorage()
        device = {"type": "lease",
                  "sd_id": "sd_id",
                  "lease_id": "lease_id"}
        with self.assertRaises(vmdevices.lease.CannotPrepare):
            vmdevices.lease.prepare(storage, [device])


class FakeStorage(object):
    """
    An object implementing the lease_info interface.
    """

    def __init__(self):
        self.leases = {}

    def lease_info(self, lease):
        key = (lease["sd_id"], lease["lease_id"])
        if key not in self.leases:
            return exception.GeneralException().response()
        return response.success(result=self.leases[key])
