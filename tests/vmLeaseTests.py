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


LEASE_DEVICES = (
    {
        "type": vmdevices.hwclass.LEASE,
        "sd_id": "sd-1",
        "lease_id": "lease-2",
        "path": "/dev/sd-1/xleases",
        "offset": 4194304
    },
    {
        "type": vmdevices.hwclass.LEASE,
        "sd_id": "sd-2",
        "lease_id": "lease-1",
        "path": "/data-center/mnt/server:_export/sd-2/dom_md/xleases",
        "offset": 3145728
    },
)


@expandPermutations
class TestDevice(XMLTestCase):

    def setUp(self):
        # TODO: replace with @skipif when available
        if not six.PY2:
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


@expandPermutations
class TestFindDevice(VdsmTestCase):

    def setUp(self):
        # TODO: replace with @skipif when available
        if not six.PY2:
            raise SkipTest("vmdevices not compatible with python 3")

    @permutations([
        ("sd-1", "lease-2"),
        ("sd-2", "lease-1"),
    ])
    def test_found(self, sd_id, lease_id):
        query = {"sd_id": sd_id, "lease_id": lease_id}
        lease = vmdevices.lease.find_device(self.devices(), query)
        self.assertEqual(lease.sd_id, sd_id)
        self.assertEqual(lease.lease_id, lease_id)

    @permutations([
        ("sd-1", "lease-1"),
        ("sd-2", "lease-2"),
    ])
    def test_lookup_error(self, sd_id, lease_id):
        query = {"sd_id": sd_id, "lease_id": lease_id}
        with self.assertRaises(LookupError):
            vmdevices.lease.find_device(self.devices(), query)

    def devices(self):
        leases = [vmdevices.lease.Device({}, self.log, **kwargs)
                  for kwargs in LEASE_DEVICES]
        return {vmdevices.hwclass.LEASE: leases}


@expandPermutations
class TestFindConf(VdsmTestCase):

    def setUp(self):
        # TODO: replace with @skipif when available
        if not six.PY2:
            raise SkipTest("vmdevices not compatible with python 3")

    @permutations([
        ("sd-1", "lease-2", 4194304),
        ("sd-2", "lease-1", 3145728),
    ])
    def test_find(self, sd_id, lease_id, offset):
        kwargs = {"type": vmdevices.hwclass.LEASE,
                  "sd_id": sd_id,
                  "lease_id": lease_id,
                  "path": "/dev/%s/xleases" % sd_id,
                  "offset": offset}
        lease = vmdevices.lease.Device({}, self.log, **kwargs)
        conf = vmdevices.lease.find_conf(self.conf(), lease)
        self.assertEqual(conf["sd_id"], sd_id)
        self.assertEqual(conf["lease_id"], lease_id)

    @permutations([
        ("sd-1", "lease-1", 3145728),
        ("sd-2", "lease-2", 4194304),
    ])
    def test_lookup_error(self, sd_id, lease_id, offset):
        kwargs = {"type": vmdevices.hwclass.LEASE,
                  "sd_id": sd_id,
                  "lease_id": lease_id,
                  "path": "/dev/%s/xleases" % sd_id,
                  "offset": offset}
        lease = vmdevices.lease.Device({}, self.log, **kwargs)
        with self.assertRaises(LookupError):
            vmdevices.lease.find_conf(self.conf(), lease)

    def conf(elf):
        return {"devices": LEASE_DEVICES}


@expandPermutations
class TestIsAttahedTo(VdsmTestCase):

    XML = """
    <domain type='kvm' id='vm-id'>
      <devices>
        <lease>
          <lockspace>sd-1</lockspace>
          <key>lease-2</key>
          <target path="/dev/sd-1/xleases" offset="4194304" />
        </lease>
        <lease>
          <lockspace>sd-2</lockspace>
          <key>lease-1</key>
          <target path="/dev/sd-2/xleases" offset="3145728" />
        </lease>
      </devices>
    </domain>
    """

    def setUp(self):
        # TODO: replace with @skipif when available
        if not six.PY2:
            raise SkipTest("vmdevices not compatible with python 3")

    @permutations([
        ("sd-1", "lease-2", 4194304),
        ("sd-2", "lease-1", 3145728),
    ])
    def test_attached(self, sd_id, lease_id, offset):
        kwargs = {"type": vmdevices.hwclass.LEASE,
                  "sd_id": sd_id,
                  "lease_id": lease_id,
                  "path": "/dev/%s/xleases" % sd_id,
                  "offset": offset}
        lease = vmdevices.lease.Device({}, self.log, **kwargs)
        self.assertTrue(lease.is_attached_to(self.XML),
                        "lease %r is not attached to %s" % (lease, self.XML))

    @permutations([
        ("sd-1", "lease-1", 3145728),
        ("sd-2", "lease-2", 4194304),
    ])
    def test_not_attached(self, sd_id, lease_id, offset):
        kwargs = {"type": vmdevices.hwclass.LEASE,
                  "sd_id": sd_id,
                  "lease_id": lease_id,
                  "path": "/dev/%s/xleases" % sd_id,
                  "offset": offset}
        lease = vmdevices.lease.Device({}, self.log, **kwargs)
        self.assertFalse(lease.is_attached_to(self.XML),
                         "lease %r is attached to %s" % (lease, self.XML))


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
