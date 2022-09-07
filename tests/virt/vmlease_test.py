# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

from vdsm.common import exception
from vdsm.common import response
from vdsm.common import xmlutils
from vdsm.virt import vmdevices

from testlib import VdsmTestCase
from testlib import XMLTestCase
from testlib import expandPermutations, permutations
import pytest


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

    def test_getxml(self):
        spec = dict(sd_id="sd_id", lease_id="lease_id", path="/path",
                    offset=1048576)
        lease = vmdevices.lease.Device(self.log, **spec)
        lease_xml = xmlutils.tostring(lease.getXML())
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
        with pytest.raises(vmdevices.lease.MissingArgument):
            vmdevices.lease.Device(self.log, **kwargs)

    def test_repr(self):
        kwargs = {"sd_id": "sd_id",
                  "lease_id": "lease_id",
                  "path": "path",
                  "offset": 0}
        lease = vmdevices.lease.Device(self.log, **kwargs)
        for key, value in kwargs.items():
            assert "%s=%s" % (key, value) in repr(lease)


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
        assert device == expected

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
        with pytest.raises(vmdevices.lease.CannotPrepare):
            vmdevices.lease.prepare(storage, [device])


@expandPermutations
class TestFindDevice(VdsmTestCase):

    @permutations([
        ("sd-1", "lease-2"),
        ("sd-2", "lease-1"),
    ])
    def test_found(self, sd_id, lease_id):
        query = {"sd_id": sd_id, "lease_id": lease_id}
        lease = vmdevices.lease.find_device(self.devices(), query)
        assert lease.sd_id == sd_id
        assert lease.lease_id == lease_id

    @permutations([
        ("sd-1", "lease-1"),
        ("sd-2", "lease-2"),
    ])
    def test_lookup_error(self, sd_id, lease_id):
        query = {"sd_id": sd_id, "lease_id": lease_id}
        with pytest.raises(LookupError):
            vmdevices.lease.find_device(self.devices(), query)

    def devices(self):
        leases = [vmdevices.lease.Device(self.log, **kwargs)
                  for kwargs in LEASE_DEVICES]
        return {vmdevices.hwclass.LEASE: leases}


@expandPermutations
class TestFindConf(VdsmTestCase):

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
        lease = vmdevices.lease.Device(self.log, **kwargs)
        conf = vmdevices.lease.find_conf(self.conf(), lease)
        assert conf["sd_id"] == sd_id
        assert conf["lease_id"] == lease_id

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
        lease = vmdevices.lease.Device(self.log, **kwargs)
        with pytest.raises(LookupError):
            vmdevices.lease.find_conf(self.conf(), lease)

    def conf(elf):
        return {"devices": LEASE_DEVICES}


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
