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
import logging

from vdsm.common.compat import subprocess

LSBLK = "/usr/bin/lsblk"
LVM = "/usr/sbin/lvm"

log = logging.getLogger("lvmfilter")


MountInfo = collections.namedtuple("MountInfo", "lv,mountpoint,devices")


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
        # Include only device mapper (253) devices. This includes both "lvm"
        # and "mpath" devices. We will filter the results later to extract only
        # the "lvm" devices.
        "--include", "253",
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
        devices = find_lv_devices(name)
        mounts.append(MountInfo(name, mountpoint, devices))

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


def format_option(items):
    """
    Format LVM filter option from the filter built by build_filter().

    Arguments:
        items (list): list of LVM device regular expression mathces.

    Returns:
        string to use in lvm.conf.
    """
    quoted = ['"' + it + '"' for it in items]
    return "filter = [ {} ]".format(", ".join(quoted))


def find_lv_devices(lv_path):
    """
    Returns list of devices used by lv lv_path.
    """
    log.debug("Looking up volume group for logical volume %r", lv_path)
    out = _run([
        LVM,
        "lvs",
        "--noheadings",
        "--readonly",
        "--options",
        "vg_name",
        lv_path
    ])
    vg_name = out.strip()
    log.debug("Looking up volume group %r devices", vg_name)
    out = _run([
        LVM,
        "vgs",
        "--noheadings",
        "--readonly",
        "--options", "pv_name",
        vg_name
    ])
    return sorted(line.strip() for line in out.splitlines())


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
    log.debug("Completed successfuly, out=%r", out)
    return out.decode("utf-8")
