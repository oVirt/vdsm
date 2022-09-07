# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

import json
from functools import partial
import os

from testlib import VdsmTestCase as TestCaseBase
from monkeypatch import MonkeyPatch, MonkeyPatchScope
from vdsm.common import commands
from vdsm.common import cmdutils
from vdsm.gluster import thinstorage

_fake_vdoCommandPath = cmdutils.CommandPath("true",
                                            "/bin/true",)


def fake_json_call(data, *args, **kw):
    return json.dumps(data).encode('utf-8')


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

        with MonkeyPatchScope([(commands, "run",
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

        with MonkeyPatchScope([(commands, "run",
                                partial(fake_json_call, data))]):
            actual = thinstorage.physicalVolumeList()
            self.assertEqual(expected, actual)

    @MonkeyPatch(thinstorage, '_vdoCommandPath', _fake_vdoCommandPath)
    def test_vdo_volume_list(self):
        expected = [
            {
                "device": "/dev/vg0/vdobase",
                "name": "/dev/mapper/vdodata",
                "size": 10737418240,
                "free": 6415798272,
                'logicalBytesUsed': 81920,
                'physicalBytesUsed': 40960
            },
            {
                "device": "/dev/vg0/vdosecond",
                "name": "/dev/mapper/vdonext",
                'logicalBytesUsed': 40960,
                'physicalBytesUsed': 15360,
                "size": 10737418240,
                "free": 6438100992
            }
        ]

        def fake_run(*args, **kwargs):
            path = os.path.join(
                os.path.dirname(__file__),
                'gluster/results/fake_vdo_status.yml'
            )
            with open(path, "rb") as f:
                out = f.read()
            return out

        with MonkeyPatchScope([(commands, "run", fake_run)]):
            actual = thinstorage.vdoVolumeList()
            self.assertEqual(expected, actual)
