# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

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


def _process_vdo_statistics(data):
    """
    Return some fields from VDO device statistics
    Example output:
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
    """
    entry = {}
    try:
        for name, stats in iteritems(data):
            blockSize = stats["block size"]
            entry["name"] = name
            entry["size"] = stats["1K-blocks"] * KiB
            entry["free"] = stats["1K-blocks available"] * KiB
            entry["logicalBytesUsed"] = (stats["logical blocks used"]
                                         * blockSize)
            entry["physicalBytesUsed"] = (stats["data blocks used"]
                                          * blockSize)
    except KeyError as e:
        log.error("Missing or invalid values in vdo data,skipping. %s", e)
        # Returning an empty object so as not to add to the device list
        return {}
    return entry


@gluster_mgmt_api
def vdoVolumeList():
    try:
        out = commands.run([_vdoCommandPath.cmd, "status"])
    except cmdutils.Error as e:
        raise ge.GlusterCmdExecFailedException(rc=e.rc, err=e.err)
    vdoData = yaml.safe_load(out)
    result = []
    for vdo, data in iteritems(vdoData["VDOs"]):
        if data["VDO statistics"] == "not available":
            log.debug("VDO statistics data not available, skipping device ")
            continue
        entry = _process_vdo_statistics(data["VDO statistics"])

        # This is a case where the vdo device does not have a corresponding
        # device mapper entry, skipping this condition without adding the
        # device to the output.
        if not entry:
            log.debug("No device mapper entry for vdo device skipping")
            continue

        entry["device"] = data["Storage device"]
        result.append(entry)

    return result
