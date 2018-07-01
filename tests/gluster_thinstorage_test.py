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
from __future__ import absolute_import
from __future__ import division

import json
from functools import partial

from testlib import VdsmTestCase as TestCaseBase
from monkeypatch import MonkeyPatch, MonkeyPatchScope
from vdsm.common import commands
from vdsm.common import cmdutils
from vdsm.gluster import thinstorage

_fake_vdoCommandPath = cmdutils.CommandPath("true",
                                            "/bin/true",)


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

    @MonkeyPatch(thinstorage, '_vdoCommandPath', _fake_vdoCommandPath)
    def test_vdo_volume_list(self):
        data = [
            "VDO status:",
            "  Date: '2018-03-02 15:55:12+02:00'",
            "  Node: hc-tiger.eng.lab.tlv.redhat.com",
            "Kernel module:",
            "  Loaded: true",
            "  Name: kvdo",
            "  Version information:",
            "    kvdo version: 6.1.0.124",
            "Configuration:",
            "  File: /etc/vdoconf.yml",
            "  Last modified: '2018-02-14 16:34:08'",
            "VDOs:",
            "  vdodata:",
            "    Compression: enabled",
            "    Configured write policy: auto",
            "    Deduplication: enabled",
            "    Device mapper status: 0 104857600 dedupe",
            "    Emulate 512 byte: enabled",
            "    Storage device: /dev/vg0/vdobase",
            "    VDO statistics:",
            "      /dev/mapper/vdodata:",
            "        1K-blocks: 10485760",
            "        1K-blocks available: 6265428",
            "        1K-blocks used: 4220332",
            "        512 byte emulation: true",
            "        write policy: async",
            "  vdonext:",
            "    Compression: enabled",
            "    Deduplication: enabled",
            "    Device mapper status: 0 104857600 dedupe",
            "    Emulate 512 byte: enabled",
            "    Storage device: /dev/vg0/vdosecond",
            "    VDO statistics:",
            "      /dev/mapper/vdonext:",
            "        1K-blocks: 10485760",
            "        1K-blocks available: 6287208",
            "        1K-blocks used: 4198552",
            "        512 byte emulation: true",
            "        write policy: async"
        ]

        expected = [
            {
                "device": "/dev/vg0/vdobase",
                "name": "/dev/mapper/vdodata",
                "size": 10737418240,
                "free": 6415798272
            },
            {
                "device": "/dev/vg0/vdosecond",
                "name": "/dev/mapper/vdonext",
                "size": 10737418240,
                "free": 6438100992
            }
        ]

        def fake_execcmd(command, raw=False, **kwargs):
            if raw:
                out = "\n".join(data)
                err = ""
            else:
                out = data
                err = []
            return 0, out, err

        with MonkeyPatchScope([(commands, "execCmd", fake_execcmd)]):
            actual = thinstorage.vdoVolumeList()
            self.assertEqual(expected, actual)
