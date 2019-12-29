#
# Copyright 2015-2018 Red Hat, Inc.
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
import logging
import yaml
from six import iteritems

from vdsm.common import cmdutils
from vdsm.common import commands
from vdsm.common.units import KiB

from . import exception as ge
from . import gluster_mgmt_api


log = logging.getLogger("Gluster")
_lvsCommandPath = cmdutils.CommandPath("lvs",
                                       "/sbin/lvs",
                                       "/usr/sbin/lvs",)
_pvsCommandPath = cmdutils.CommandPath("pvs",
                                       "/sbin/pvs",
                                       "/usr/sbin/pvs",)
_vdoCommandPath = cmdutils.CommandPath("vdo",
                                       "/bin/vdo",
                                       "/usr/bin/vdo",)


@gluster_mgmt_api
def logicalVolumeList():
    try:
        out = commands.run([_lvsCommandPath.cmd,
                            "--reportformat", "json",
                            "--units", "b",
                            "--nosuffix",
                            "-o",
                            "lv_size,data_percent,lv_name,vg_name,pool_lv"])
    except cmdutils.Error as e:
        raise ge.GlusterCmdExecFailedException(e.rc, e.err)
    volumes = []
    for lv in json.loads(out)["report"][0]["lv"]:
        lv["lv_size"] = int(lv["lv_size"])
        if not lv["pool_lv"] and lv["data_percent"]:
            lv["lv_free"] = int(
                (1 - float(lv.pop("data_percent")) / 100) * lv["lv_size"]
            )
        else:
            lv["lv_free"] = 0
            lv.pop("data_percent")
        volumes.append(lv)
    return volumes


@gluster_mgmt_api
def physicalVolumeList():
    try:
        out = commands.run([_pvsCommandPath.cmd,
                            "--reportformat", "json",
                            "--units", "b", "--nosuffix",
                            "-o", "pv_name,vg_name"])
    except cmdutils.Error as e:
        raise ge.GlusterCmdExecFailedException(e.rc, e.err)
    return json.loads(out)["report"][0]["pv"]


@gluster_mgmt_api
def vdoVolumeList():
    try:
        out = commands.run([_vdoCommandPath.cmd, "status"])
    except cmdutils.Error as e:
        raise ge.GlusterCmdExecFailedException(e.rc, e.err)
    vdoData = yaml.safe_load(out)
    result = []
    for vdo, data in iteritems(vdoData["VDOs"]):
        entry = {}
        entry["device"] = data["Storage device"]
        for mapper, stats in iteritems(data["VDO statistics"]):
            blockSize = stats["block size"]
            entry["name"] = mapper
            entry["size"] = stats["1K-blocks"] * KiB
            entry["free"] = stats["1K-blocks available"] * KiB
            entry["logicalBytesUsed"] = (
                stats["logical blocks used"] * blockSize
            )
            entry["physicalBytesUsed"] = (
                stats["data blocks used"] * blockSize
            )
        result.append(entry)

    return result
