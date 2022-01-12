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
Manage LVM devices and LVM devices file.

This module provides the infrastructure for configuring LVM devices on a
host, ensuring that LVM can access only the devices needed by the host
mounted filesystems, and cannot access logical volumes on shared
storage, which are owned by vdsm. This is going to be replacement of LVM
filter.

The module should be used from command line running as root such as
vdsm-tool.
"""

import logging
import os
import subprocess

from vdsm.common import cmdutils
from vdsm.common import commands
from vdsm.storage import lvmconf
from vdsm.storage import lvmfilter

LVM = "/usr/sbin/lvm"
_LVM_SYSTEM_DEVICES_PATH = "/etc/lvm/devices/system.devices"


log = logging.getLogger("lvmdevices")


def is_configured():
    """
    Return True if lvm is configured to use devices file, which means that it's
    enabled in lvm configuration and devices file must exists.
    """
    return _enabled() and _devices_file_exists()


def configure(vgs):
    """
    Configure lvm to use devices file and create initial system devices file.
    When we succeed, remove lvm filter if there is any, as it's not needed
    any more.
    """
    # Always configure devices file. File maybe be empty or not up to date.
    # On the other hand configuring correct devices file doesn't cause any
    # harm.
    try:
        _create_system_devices(vgs)
    except cmdutils.Error:
        log.warning("Failed to create system devices file.")
        raise

    # Devices file was created, enable devices/use_devicesfile in lvm config.
    log.debug("Enabling lvm devices/use_devicesfile.")
    _configure_devices_file(enable=True)

    # Devices file is now configured and enabled, check if it's valid and
    # inform the user if it's not.
    _run_check()

    # We are done with configuration of lvm devices file and lvm filter is not
    # needed/used any more. Remove it, if there is any.
    lvmfilter.remove_filter()


def _enabled():
    """
    Return True if lvm is configured to use devices file. Devices file itself
    is not needed to be configured as it has predefined value by lvm.
    """
    with lvmconf.LVMConfig() as config:
        use_devicesfile = config.getint("devices", "use_devicesfile") == 1
        return use_devicesfile


def _devices_file_exists():
    """
    Returns True if default lvm devices file exists. If the file doesn't
    exists, lvm disables whole devices file functionality.
    """
    return os.path.exists(_LVM_SYSTEM_DEVICES_PATH)


def _configure_devices_file(enable=True):
    """
    Configure lvm to use devices file or disable it.
    """
    enabled = 1 if enable else 0
    with lvmconf.LVMConfig() as config:
        config.setint("devices", "use_devicesfile", enabled)
        config.save()


def _create_system_devices(vgs):
    """
    Import devices of provided VGs into LVM devices file.
    """
    for vg in vgs:
        _run_vgimportdevices(vg)


def _run_vgimportdevices(vg):
    """
    Import underlying devices of provided VG into LVM devices file. Import is
    done using vgimportdevices command. vgimportdevices takes into account
    existing lvm filter, so if some devices are excluded by the filter, such
    devices won't be imported. If the filter is wrong, we may miss some
    devices. To avoid such situation, set the filter to enable all the devices.
    """
    cmd = [LVM,
           'vgimportdevices',
           vg,
           '--config',
           'devices { use_devicesfile = 1 filter = ["a|.*|"] }'
           ]

    p = commands.start(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )
    out, err = commands.communicate(p)

    if p.returncode == 0 and err:
        log.warning("Command %s succeeded with warnings: %s", cmd, err)

    if p.returncode != 0:
        raise cmdutils.Error(cmd, p.returncode, out, err)


def _run_check():
    """
    Check the devices file. As, according to LVM developers, the behavior of
    this functionality is not entirely or strictly well defined yet, we don't
    raise any exception if the check finds issues in devices file, but only
    log a waring with found issues.
    """
    cmd = [LVM, 'lvmdevices', "--check"]

    p = commands.start(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )
    out, err = commands.communicate(p)

    if p.returncode == 0 and err:
        log.warning("Found following issues in LVM devices file: %s", err)

    if p.returncode != 0:
        raise cmdutils.Error(cmd, p.returncode, out, err)
