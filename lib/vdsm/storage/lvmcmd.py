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

import re
import logging

from itertools import chain

from vdsm import constants
from vdsm.common import commands
from vdsm.common.compat import subprocess
from vdsm.config import config
from vdsm.storage import exception as se

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

USER_DEV_LIST = [d for d in config.get("irs", "lvm_dev_whitelist").split(",")
                 if d is not None]

USE_DEVICES = config.get("lvm", "config_method").lower() == "devices"


class LVMRunner(object):
    """
    Does actual execution of the LVM command and handle output, e.g. decode
    output or log warnings.
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

    def run(self, cmd):
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

        rc, out, err = self._run_command(cmd)

        out = out.decode("utf-8").splitlines()
        err = err.decode("utf-8").splitlines()

        err = [s for s in err if not self.SUPPRESS_WARNINGS.search(s)]

        if rc == 0 and err:
            log.warning("Command %s succeeded with warnings: %s", cmd, err)

        if rc != 0:
            raise se.LVMCommandError(cmd, rc, out, err)

        return out

    def _run_command(self, cmd):
        p = commands.start(
            cmd,
            sudo=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        out, err = commands.communicate(p)
        return p.returncode, out, err


_runner = LVMRunner()


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
    Format and run LVM command with the given devices through the LVMRunner.
    May raise se.LVMCommandError from the LVMRunner.

    Args:
        cmd (List[str]): Specific LVM command that will be run.
        devices (List[str]): List of devices to add to the CLI.
        use_lvmpolld (bool): use_lvmpolld CLI option.

    Returns:
        str: LVM command output.
    """
    full_cmd = _addExtraCfg(cmd, devices, use_lvmpolld=use_lvmpolld)
    return _runner.run(full_cmd)
