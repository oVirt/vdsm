#
# Copyright 2012-2018 Red Hat, Inc.
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

import json
from functools import partial

from testlib import VdsmTestCase as TestCaseBase
from monkeypatch import MonkeyPatchScope
from vdsm.common import commands
from vdsm.gluster import thinstorage


def fake_json_call(data, cmd, **kw):
    return 0, [json.dumps(data)], []


class GlusterStorageDevTest(TestCaseBase):

    def test_logical_volume_list(self):
        data = {
            "report": [
                {
                    "lv": [
                        {
                            "lv_size": "52613349376",
                            "data_percent": "0.06",
                            "lv_name": "internal_pool",
                            "vg_name": "INTERNAL",
                            "pool_lv": ""
                        },
                        {
                            "lv_size": "10737418240",
                            "data_percent": "0.16",
                            "lv_name": "vdodistr",
                            "vg_name": "INTERNAL",
                            "pool_lv": "internal_pool"
                        },
                        {
                            "lv_size": "53687091200",
                            "data_percent": "",
                            "lv_name": "engine",
                            "vg_name": "vg0",
                            "pool_lv": ""
                        }
                    ]
                }
            ]
        }

        expected = [
            {
                "lv_size": 52613349376,
                "lv_free": 52581781366,
                "lv_name": "internal_pool",
                "vg_name": "INTERNAL",
                "pool_lv": ""
            },
            {
                "lv_size": 10737418240,
                "lv_free": 0,
                "lv_name": "vdodistr",
                "vg_name": "INTERNAL",
                "pool_lv": "internal_pool"
            },
            {
                "lv_size": 53687091200,
                "lv_free": 0,
                "lv_name": "engine",
                "vg_name": "vg0",
                "pool_lv": ""
            }
        ]

        with MonkeyPatchScope([(commands, "execCmd",
                                partial(fake_json_call, data))]):
            actual = thinstorage.logicalVolumeList()
            self.assertEqual(expected, actual)

    def test_physical_volume_list(self):
        data = {
            "report": [
                {
                    "pv": [
                        {
                            "pv_name": "/dev/mapper/vdodata",
                            "vg_name": "INTERNAL"
                        },
                        {
                            "pv_name": "/dev/sdb1",
                            "vg_name": "vg0"
                        }
                    ]
                }
            ]

        }

        expected = [
            {"pv_name": "/dev/mapper/vdodata", "vg_name": "INTERNAL"},
            {"pv_name": "/dev/sdb1", "vg_name": "vg0"}
        ]

        with MonkeyPatchScope([(commands, "execCmd",
                                partial(fake_json_call, data))]):
            actual = thinstorage.physicalVolumeList()
            self.assertEqual(expected, actual)
