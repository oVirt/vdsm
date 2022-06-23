#
# Copyright 2022 Red Hat, Inc.
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
Wrapper for running and formatting LVM commands.
"""

import os
import re
import logging

from enum import Enum, auto
from itertools import chain

from vdsm import constants
from vdsm.common import commands
from vdsm.common.compat import subprocess
from vdsm.config import config
from vdsm.storage import exception as se
from vdsm.storage.devicemapper import DMPATH_PREFIX

log = logging.getLogger("storage.lvmcmd")


# Runtime configuration notes
# ===========================
#
# This configuration is used for all commands using --config option. This
# overrides various options built into lvm comamnds, or defined in
# /etc/lvm/lvm.conf or /etc/lvm/lvmlocl.conf.
#
# hints="none"
# ------------
# prevent from lvm to remember which devices are PVs so that lvm can avoid
# scanning other devices that are not PVs, since we create and remove PVs from
# other hosts, then the hints might be wrong.  Finally because oVirt host is
# like to use strict lvm filter, the hints are not needed.  Disable hints for
# lvm commands run by vdsm, even if hints are enabled on the host.
#
# obtain_device_list_from_udev=0
# ------------------------------
# Avoid random faiures in lvcreate an lvchange seen during stress tests
# (using tests/storage/stress/reload.py). This was disabled in RHEL 6, and
# enabled back in RHEL 7, and seems to be broken again in RHEL 8.

LVMCONF_TEMPLATE = """
devices {
 preferred_names=["^/dev/mapper/"]
 ignore_suspended_devices=1
 write_cache_state=0
 disable_after_error_count=3
 %(filter)s
 hints="none"
 obtain_device_list_from_udev=0
}
global {
 prioritise_write_locks=1
 wait_for_locks=1
 use_lvmpolld=%(use_lvmpolld)s
}
backup {
 retain_min=50
 retain_days=0
}
"""

# Warnings written to LVM stderr that should not be logged as warnings.
SUPPRESS_WARNINGS = re.compile(
    "|".join([
        "WARNING: This metadata update is NOT backed up",
        (r"WARNING: ignoring metadata seqno \d+ on /dev/mapper/\w+ for "
            r"seqno \d+ on /dev/mapper/\w+ for VG \w+"),
        r"WARNING: Inconsistent metadata found for VG \w+",
        ("WARNING: Activation disabled. No device-mapper interaction "
            "will be attempted"),
    ]),
    re.IGNORECASE)

USER_DEV_LIST = [d for d in config.get("irs", "lvm_dev_whitelist").split(",")
                 if d is not None]

USE_DEVICES = config.get("lvm", "config_method").lower() == "devices"

LVM_NOBACKUP = ("--autobackup", "n")


class AutoName(Enum):
    @staticmethod
    def _generate_next_value_(name, start, count, last_values):
        return name


class LVMCmd(str, AutoName):
    lvcreate = auto()
    lvremove = auto()
    lvchange = auto()
    lvextend = auto()
    lvreduce = auto()

    def __str__(self):
        return self.value


def _run(cmd):
    """
    Run LVM command, logging warnings for successful commands.

    An example case is when LVM decide to fix VG metadata when running a
    command that should not change the metadata on non-SPM host. In this
    case LVM will log this warning:

        WARNING: Inconsistent metadata found for VG xxx-yyy-zzz - updating
        to use version 42

    We log warnings only for successful commands since callers are already
    handling failures.
    """

    rc, out, err = _run_command(cmd)

    out = out.decode("utf-8").splitlines()
    err = err.decode("utf-8").splitlines()

    err = [s for s in err if not SUPPRESS_WARNINGS.search(s)]

    if rc == 0 and err:
        log.warning("Command %s succeeded with warnings: %s", cmd, err)

    if rc != 0:
        raise se.LVMCommandError(cmd, rc, out, err)

    return out


def _run_command(cmd):
    p = commands.start(
        cmd,
        sudo=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )
    out, err = commands.communicate(p)
    return p.returncode, out, err


def _prepare_device_set(devs):
    devices = set(d.strip() for d in chain(devs, USER_DEV_LIST))
    devices.discard('')
    if devices:
        devices = sorted(d.replace(r'\x', r'\\x') for d in devices)
    return devices


def _buildFilter(devices):
    if devices:
        # Accept specified devices, reject everything else.
        # ["a|^/dev/1$|^/dev/2$|", "r|.*|"]
        pattern = "|".join("^{}$".format(d) for d in devices)
        accept = '"a|{}|", '.format(pattern)
    else:
        # Reject all devices.
        # ["r|.*|"]
        accept = ''
    return '[{}"r|.*|"]'.format(accept)


def _buildConfig(dev_filter="", use_lvmpolld="1"):
    if dev_filter:
        dev_filter = f"filter={dev_filter}"

    conf = LVMCONF_TEMPLATE % {
        "filter": dev_filter,
        "use_lvmpolld": use_lvmpolld,
    }
    return conf.replace("\n", " ").strip()


def _addExtraCfg(cmd, devices, use_lvmpolld):
    newcmd = [constants.EXT_LVM, cmd[0]]

    device_set = _prepare_device_set(devices)

    if USE_DEVICES:
        if device_set:
            newcmd += ["--devices", ",".join(device_set)]
        dev_filter = ""
    else:
        dev_filter = _buildFilter(device_set)

    conf = _buildConfig(
        dev_filter=dev_filter,
        use_lvmpolld="1" if use_lvmpolld else "0")
    newcmd += ["--config", conf]

    if len(cmd) > 1:
        newcmd += cmd[1:]

    return newcmd


def run(cmd, devices, use_lvmpolld=True):
    """
    Format and run LVM command with the given devices.
    May raise se.LVMCommandError.

    Args:
        cmd (List[str]): Specific LVM command that will be run.
        devices (List[str]): List of devices to add to the CLI.
        use_lvmpolld (bool): use_lvmpolld CLI option.

    Returns:
        str: LVM command output.
    """
    full_cmd = _addExtraCfg(cmd, devices, use_lvmpolld=use_lvmpolld)
    return _run(full_cmd)


def fqpvname(pv):
    if os.path.isabs(pv):
        # Absolute path, use as is.
        return pv
    else:
        # Multipath device guid
        return os.path.join(DMPATH_PREFIX, pv)


def lvcreate(vg_name, lv_name, size, contiguous, initial_tags, device):
    cont = {True: "y", False: "n"}[contiguous]
    cmd = [str(LVMCmd.lvcreate)]
    cmd.extend(LVM_NOBACKUP)
    cmd.extend(("--contiguous", cont, "--size", "%sm" % size))
    # Disable wiping signatures, enabled by default in RHEL 8.4. We own the VG
    # and the LVs and we know it is alwasy safe to zero a new LV. With this
    # option, LVM will zero the first 4k of the device without confirmation.
    # See https://bugzilla.redhat.com/1946199.
    cmd.extend(("--wipesignatures", "n"))
    for tag in initial_tags:
        cmd.extend(("--addtag", tag))
    cmd.extend(("--name", lv_name, vg_name))
    if device is not None:
        cmd.append(fqpvname(device))
    return cmd


def lvremove(vg_name, lv_names):
    # Fix me:removes active LVs too. "-f" should be removed.
    cmd = [str(LVMCmd.lvremove), "-f"]
    cmd.extend(LVM_NOBACKUP)
    for lv_name in lv_names:
        cmd.append(f"{vg_name}/{lv_name}")
    return cmd


def lvchange(vg, lvs, attrs, autobackup=False):
    # If it fails or not we (may be) change the lv,
    # so we invalidate cache to reload these volumes on first occasion
    lvnames = tuple(f"{vg}/{lv}" for lv in lvs)
    cmd = [str(LVMCmd.lvchange)]
    if not autobackup:
        cmd.extend(LVM_NOBACKUP)
    if isinstance(attrs[0], str):
        # ("--attribute", "value")
        cmd.extend(attrs)
    else:
        # (("--aa", "v1"), ("--ab", "v2"))
        for attr in attrs:
            cmd.extend(attr)
    cmd.extend(lvnames)
    return cmd


def lvextend(vg_name, lv_name, size_mb, refresh):
    cmd = [str(LVMCmd.lvextend)]
    cmd.extend(LVM_NOBACKUP)
    if not refresh:
        cmd.extend(("--driverloaded", "n"))
    cmd.extend(("--size", f"{size_mb}m", f"{vg_name}/{lv_name}"))
    return cmd


def lvreduce(vg_name, lv_name, size_mb, force):
    cmd = [str(LVMCmd.lvreduce)]
    cmd.extend(LVM_NOBACKUP)
    if force:
        cmd.append("--force")
    cmd.extend(("--size", f"{size_mb}m", f"{vg_name}/{lv_name}"))
    return cmd
