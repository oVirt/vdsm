#
# Copyright 2017 Red Hat, Inc.
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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#

"""
lvmfilter - Generate LVM filter

This module provides the infrastructure for configuring LVM filter on a
host, ensuring that LVM can access only the devices needed by the host
mounted filesystems, and cannot access logical volumes on shared
storage, which are owned by Vdsm.

The module should be used from command line running as root such as
vdsm-tool.

This module really belongs in LVM or anaconda. Limiting the vdsm
dependencies to make it easy to take out of vdsm in the future.

For more info why LVM filter is needed, see
https://bugzilla.redhat.com/1449968
"""

from __future__ import absolute_import

import collections
import itertools
import json
import logging
import operator
import os
import re
import subprocess

from vdsm import constants
from vdsm.common import errors
from vdsm.common import udevadm
from vdsm.storage import lvmconf

LSBLK = "/usr/bin/lsblk"
PROC_DEVICES = "/proc/devices"
SYS_BLOCK_DEVICE_PATTERN = "/sys/block/{}/device/subsystem"
WWID_ATTRIBUTE = {
    "scsi": "ID_SERIAL",
    "nvme": "ID_WWN",
    "ccw": "ID_UID"
}

log = logging.getLogger("lvmfilter")


MountInfo = collections.namedtuple(
    "MountInfo", "lv,mountpoint,vg_name,devices")
FilterItem = collections.namedtuple("FilterItem", "action,path")
Advice = collections.namedtuple("Advice", "action,filter,wwids")

# We use this tag to detect a mounted ovirt storage domain - typically the
# master lv of a block storage domain.
OVIRT_VG_TAG = "RHAT_storage_domain"

# Advice actions

# The host is already configured, no action is needed.
UNNEEDED = "unneeded"

# We could determine a new configuration for this host, adding or replacing the
# current LVM filter. Configuring the filter automatically is safe.
CONFIGURE = "configure"

# We do not fully understand the current filter, so we are not going to replace
# it. The user need to configure the filter manually, possibly modifying the
# filter, or consult support.
RECOMMEND = "recommend"


class InvalidFilter(Exception):
    msg = "Invalid LVM filter regex {self.regex!r}: {self.reason}"

    def __init__(self, regex, reason):
        self.regex = regex
        self.reason = reason

    def __str__(self):
        return self.msg.format(self=self)


class NoDeviceMapperMajorNumber(errors.Base):
    msg = "Cannot determine major number for device-mapper devices"


class UnsupportedSubsystemType(errors.Base):
    msg = "{self.device!r} has unsupported subsystem type {self.type!r}"

    def __init__(self, device, type):
        self.device = device
        self.type = type


def dm_major_number():
    """
    Finds major number for device-mapper type devices. In most of the case
    the major number is 253, but it can vary, so better to find this number out
    on each system.
    """
    with open(PROC_DEVICES) as f:
        dm = re.search(r"^\s*(\d+)\s+device-mapper$", f.read(), re.MULTILINE)
    if not dm:
        raise NoDeviceMapperMajorNumber()
    return dm.group(1)


def find_lvm_mounts():
    """
    Found mounted logical volumes and the underlying block devices required for
    these mounts. Based on the results, you can build LVM filter for this host.

    Must run as root since it uses LVM to lookup the underlying devices.

    Returns:
        sorted list of MountInfo objects.
    """
    log.debug("Looking up mounted logical volumes")

    out = _run([
        LSBLK,
        # Produce output in raw format.
        "--raw",
        # Do not print a header line.
        "--noheadings",
        # Print full device paths.
        "--paths",
        # Print dependencies in inverse order, for example:
        # /dev/mapper/vg0-lv_root
        # `-/dev/vda2
        #   `-/dev/vda
        # With this we can --include only device mapper top devices.
        "--inverse",
        # Include only device mapper devices. This includes both "lvm"
        # and "mpath" devices. We will filter the results later to extract only
        # the "lvm" devices.
        "--include", dm_major_number(),
        # Do not print holder devices or slaves, since we are interested only
        # in lvm devices.
        "--nodeps",
        # Specify which output columns to print.
        "--output", "type,name,mountpoint",
    ])

    # Format is: devtype space name space [mountpoint]\n
    rows = [line.rstrip("\n").split(" ") for line in out.splitlines()]

    mounts = []
    for devtype, name, mountpoint in rows:
        if devtype != "lvm" or mountpoint == "":
            continue
        vg_name, tags = vg_info(name)
        if OVIRT_VG_TAG in tags:
            log.debug("Skipping oVirt logical volume %r", name)
            continue
        devices = vg_devices(vg_name)
        mounts.append(MountInfo(name, mountpoint, vg_name, devices))

    # Keep sorted for easy testing.
    return sorted(mounts)


def build_filter(mounts):
    """
    Builds LVM filter from the output of find_lvm_mounts(). The output
    can be used to configure lvm.conf with the augeas library.

    To format the filter option for configuring lvm.conf manually or for
    display purpose, use format_option().

    Returns:
        List of LVM device regular expressions matches.
    """
    log.debug("Building filter for %s", mounts)

    devices = set()
    for mnt in mounts:
        for dev in mnt.devices:
            devices.add(dev)

    items = []

    # First accept the required devices
    for device in sorted(devices):
        items.append("a|^{}$|".format(device))

    # Reject anything else.
    items.append("r|.*|")

    return items


def find_disks(devices):
    """
    Find the underlying disk devices of the queried lvm devices.
    If the queried device is a multipath device, its resolution is omitted
    from the output as we look only for disk devices not already managed by
    multipath.

    This is done by reaching to the inner children level of the following
    lsblk output for the device:

    # lsblk -o NAME,TYPE --json --inverse --paths /dev/sda2
    {
        "blockdevices": [
            {"name": "/dev/sda2", "type": "part",
                "children": [
                    {"name": "/dev/sda", "type": "disk"}
                ]
            }
        ]
    }

    Returns:
        A set of full device names of disk typed devices.
    """
    log.debug("Looking up disk devices")

    cmd = [
        LSBLK,
        # Provide output in a json dictionary format.
        "--json",
        # Show the underlying devices below the queried /dev/mapper device.
        "--inverse",
        # Use full path names for devices.
        "--paths",
        # output device name and type.
        "--output", "NAME,TYPE"
    ]
    cmd.extend(devices)

    out = _run(cmd)
    info = json.loads(out)
    disks = set()

    _search_disks(info["blockdevices"], disks)
    return disks


def _search_disks(devices, disks):
    for device in devices:
        if device["type"] == "mpath":
            log.debug("Skipping multipath device %s", device["name"])
            continue
        if device["type"] == "disk":
            log.debug("Found disk device %s", device["name"])
            disks.add(device["name"])
        elif "children" in device:
            log.debug("Searching disks under device %s", device["name"])
            _search_disks(device["children"], disks)


def find_wwids(mounts):
    """
    Get disks WWIDs for the devices of find_lvm_mounts(). The output
    can be used to configure multipath blacklist for disk devices used
    for locally mounted LVs.

    Returns:
        Set of WWID strings.
    """
    devices = set()
    for mnt in mounts:
        devices.update(mnt.devices)

    wwids = set()
    for disk in find_disks(devices):
        try:
            wwid = _resolve_wwid(disk)
        except UnsupportedSubsystemType as e:
            log.debug(e)
            continue

        if not wwid:
            log.debug("No WWID was found for disk %s", disk)
            continue
        wwids.add(wwid)

    return wwids


def _resolve_wwid(disk):
    """
    Resolve WWID for a given disk device. Resolution is done by first looking
    for the disk subsystem type and then retrieving the corresponding value
    for the right key in the output of 'udevadm info <device-name>' as
    different subsystem type devices WWIDs are identified by multipath by
    different keys in the info output; multipath uses ID_SERIAL for SCSI
    devices, ID_UID for DASD devices, and ID_WWN for NVMe devices.

    Returns:
        WWID string.
    """
    path = SYS_BLOCK_DEVICE_PATTERN.format(os.path.basename(disk))
    device_type = os.path.basename(os.path.realpath(path))

    attr = WWID_ATTRIBUTE.get(device_type)
    if not attr:
        raise UnsupportedSubsystemType(disk, device_type)

    out = udevadm.info(disk)
    match = re.search(r"^{}=(.+)$".format(attr), out, re.MULTILINE)
    if match:
        return match.group(1)


def analyze(current_filter, wanted_filter, current_wwids, wanted_wwids):
    """
    Analyze LVM filter wanted and current configuration, and advice how to
    proceed.

    Returns:
        An Advice object
    """

    # This is the expected condition when running on a host for the first time.
    if not current_filter:
        return Advice(CONFIGURE, wanted_filter, wanted_wwids)

    if current_filter == wanted_filter:
        # Same filter, ignoring whitespace and quoting difference.
        if current_wwids != wanted_wwids:
            # But we still need to update the blacklist.
            return Advice(CONFIGURE, wanted_filter, wanted_wwids)
        return Advice(UNNEEDED, None, None)

    # Is this a syntax difference?
    wanted_items = [parse_item(r) for r in wanted_filter]

    # This may raise if the current filter is invalid. We are not going to
    # touch invalid LVM configuration.
    current_items = [parse_item(r) for r in current_filter]

    if current_items == wanted_items:
        # Same filter, different delimiter syntax. For example:
        # "a|^/dev/sda2$|" == "a/^dev/sda2$/".
        if current_wwids != wanted_wwids:
            # But we still need to update the blacklist.
            return Advice(CONFIGURE, wanted_filter, wanted_wwids)
        return Advice(UNNEEDED, None, None)

    # Is this order difference?
    wanted_items = normalize_items(wanted_items)
    current_items = normalize_items(current_items)

    if current_items == wanted_items:
        # This filters are the same, using different order.
        if current_wwids != wanted_wwids:
            # But we still need to update the blacklist.
            return Advice(CONFIGURE, wanted_filter, wanted_wwids)
        return Advice(UNNEEDED, None, None)

    # Is filter using udev links (/dev/disk/by-id/...) instead of device names
    # (/dev/sda2)? If the list of items is same after resolving paths, we can
    # replace current filter with the wanted filter.
    current_resolved = resolve_devices(current_items)
    wanted_resolved = resolve_devices(wanted_items)

    if current_resolved == wanted_resolved:
        return Advice(CONFIGURE, wanted_filter, wanted_wwids)

    # The current filter intent is different. We take the safe way - the user
    # knows better. We will recommend to configure our filter, but the user
    # will have to do this, or maybe contact support.
    return Advice(RECOMMEND, wanted_filter, wanted_wwids)


def normalize_items(items):
    """
    Sort consecutive items of same type, normalizing equivalent filters with
    different order, that have the same intent.

    Example input:

        [
            FilterItem("a", "/dev/c"),
            FilterItem("a", "/dev/a"),
            FilterItem("a", "/dev/b"),
            FilterItem("r", ".*"),
        ]

    Example output:

        [
            FilterItem("a", "/dev/a"),
            FilterItem("a", "/dev/b"),
            FilterItem("a", "/dev/c"),
            FilterItem("r", ".*"),
        ]
    """
    res = []
    for k, g in itertools.groupby(items, operator.attrgetter("action")):
        res.extend(sorted(g))
    return res


def parse_item(regex):
    action = regex[0]
    if action not in ("a", "r"):
        raise InvalidFilter(
            regex,
            "regex must be preceded by 'a' to accept the path, or by 'r' "
            "to reject the path")

    path = regex[1:]
    if path[0] != path[-1]:
        raise InvalidFilter(
            regex,
            "regex must be delimited by a vertical bar '|' (or any "
            "character)")

    path = path[1:-1]
    if not path:
        raise InvalidFilter(regex, "Empty path")

    return FilterItem(action, path)


def format_option(items):
    """
    Format LVM filter option from the filter built by build_filter().

    Arguments:
        items (list): list of LVM device regular expression matches.

    Returns:
        string to use in lvm.conf.
    """
    quoted = ['"' + it + '"' for it in items]
    return "filter = [ {} ]".format(", ".join(quoted))


def vg_info(lv_path):
    """
    Returns list of devices used by lv lv_path.
    """
    log.debug("Looking up information for logical volume %r", lv_path)
    out = _run([
        constants.EXT_LVM,
        "lvs",
        "--noheadings",
        "--readonly",
        # If the host was already configured, the lvm filter hides the devices
        # of the mounted master lv, and lvs will fail. Use a permissive filter
        # to avoid this. Also, run lvs with devices file disabled. This allows
        # us to avoid warnings that filter should be used while devices file is
        # enabled. This can happen when lvm is configured to use devices file,
        # but vdsm is configured to use filter.
        "--config", 'devices {use_devicesfile = 0 filter=["a|.*|"]}',
        "--options", "vg_name,vg_tags",
        lv_path
    ])
    # Format is: space space vg_name space tag,tag... newline
    out = out.lstrip().rstrip("\n")
    vg_name, vg_tags = out.split(" ", 1)
    vg_tags = vg_tags.split(",")
    return vg_name, vg_tags


def vg_devices(vg_name):
    """
    Returns list of devices used by vg vg_name.
    """
    log.debug("Looking up volume group %r devices", vg_name)
    out = _run([
        constants.EXT_LVM,
        "vgs",
        "--noheadings",
        "--readonly",
        # If the host has an incorrect filter, some devices needed by the host
        # may be hidden, preventing creating of a new correct filter. Also, run
        # lvs with devices file disabled. This allows us to avoid warnings that
        # filter should be used while devices file is enabled. This can happen
        # when lvm is configured to use devices file, but vdsm is configured to
        # use filter.
        "--config", 'devices {use_devicesfile = 0 filter=["a|.*|"]}',
        "--options", "pv_name",
        vg_name
    ])
    return sorted(line.strip() for line in out.splitlines())


def resolve_devices(filter_items):
    """
    Resolves absolute paths in the filter items if possible, otherwise keeps
    paths intact. Resolved paths are returned in normalized order.

    Example input:

        [
            FilterItem("a", "^/dev/a$"),
            FilterItem("a", "^/dev/disk/by-id/lvm-pv-uuid-b^"),
            FilterItem("a", "^/dev/c*"),
            FilterItem("r", ".*"),
        ]

    Example output:

        [
            FilterItem("a", "^/dev/a$"),
            FilterItem("a", "^/dev/b$"),
            FilterItem("a", "^/dev/c*"),
            FilterItem("r", ".*"),
        ]
    """
    resolved_items = []
    for r in filter_items:
        path = r.path
        reg_exp_start = ""
        reg_exp_end = ""

        if path.startswith("^"):
            reg_exp_start = "^"
            path = path[1:]
        if path.endswith("$"):
            reg_exp_end = "$"
            path = path[:-1]

        if path.startswith("/"):
            # Resolve absolute paths.
            resolved_path = os.path.realpath(path)
            reg_exp = reg_exp_start + resolved_path + reg_exp_end
            resolved_items.append(FilterItem(r.action, reg_exp))
        else:
            # Not an absolute path, leave it as is.
            resolved_items.append(FilterItem(r.action, r.path))

    return normalize_items(resolved_items)


def remove_filter():
    """
    Remove LVM filter from LVM configuration file.
    """
    with lvmconf.LVMConfig() as config:
        current_filter = config.getlist("devices", "filter")
        if current_filter:
            config.remove("devices", "filter")
            config.save()


def _run(args):
    """
    Run command, returning command output.

    Arguments:
        args (sequence): Program arguments
    Raises:
        subprocess32.CalledProcessError if the command terminated with non-zero
            exit code.
    Returns:
        Command output decoded using utf-8.
    """
    log.debug("Running %s", args)
    out = subprocess.check_output(args)
    log.debug("Completed successfully, out=%r", out)
    return out.decode("utf-8")
