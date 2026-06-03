# SPDX-FileCopyrightText: oVirt Developers
# SPDX-License-Identifier: GPL-2.0-or-later

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

import datetime
import logging
import os
import subprocess

from vdsm import constants
from vdsm.common import cmdutils
from vdsm.common import commands
from vdsm.storage import constants as sc
from vdsm.storage import fileUtils
from vdsm.storage import lvmconf
from vdsm.storage import lvmfilter

_LVM_DEVICES_DIR = "/etc/lvm/devices"


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
    _create_system_devices(vgs)

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
    try:
        devices_file = _get_devices_file_path()
    except (cmdutils.Error, lvmconf.UnexpectedLvmConfigOutput):
        return False

    return os.path.exists(devices_file)


def _get_devices_file_path():
    devicesfile = lvmconf.configured_value("devices", "devicesfile")
    return os.path.join(_LVM_DEVICES_DIR, devicesfile)


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
    Import the VGs in `vgs` (the VGs that back the host's mounts, from
    lvmfilter.find_lvm_mounts) plus any other VGs already visible to
    lvm, so external storage VGs are not evicted when vdsm rebuilds
    the devices file.
    """
    devices_file = _get_devices_file_path()

    existing_vgs = ()
    if not _devices_file_has_entries(devices_file):
        # No device entries yet. List visible VGs so external storage
        # is preserved on the use_devicesfile=1 transition.
        existing_vgs = _list_all_visible_vgs()
        if not existing_vgs and not vgs:
            # No VGs anywhere. Drop a header so lvm does not scan every
            # device under use_devicesfile=1.
            now = datetime.datetime.now()
            data = (
                f"# Created by Vdsm pid {os.getpid()} at "
                f"{now.strftime('%a %b %d %H:%M:%S %Y')}\n"
            )
            os.makedirs(os.path.dirname(devices_file), exist_ok=True)
            fileUtils.atomic_write(devices_file, data.encode("utf-8"))
            return

    # Import visible and requested VGs, filtering out duplicate VGs.
    for vg in sorted(set(existing_vgs) | set(vgs)):
        _run_vgimportdevices(vg)


def _devices_file_has_entries(devices_file):
    """
    Return True if the devices file records at least one device entry
    (an IDTYPE= line). A missing or empty file, or one holding only
    comments and bare lvm metadata (VERSION=, PRODUCT_UUID=) with no
    device entries, counts as no entries.
    """
    try:
        with open(devices_file) as f:
            for line in f:
                if line.strip().startswith("IDTYPE="):
                    return True
    except FileNotFoundError:
        return False
    return False


def _list_all_visible_vgs():
    """
    Return the sorted VG names lvm can see, bypassing the devices file
    so VGs created while use_devicesfile was off are still surfaced.
    Skips oVirt SD VGs (sc.STORAGE_DOMAIN_TAG), like
    lvmfilter.find_lvm_mounts.
    """
    cmd = [
        constants.EXT_LVM,
        'vgs',
        '--noheadings',
        '--readonly',
        '-o',
        'vg_name,vg_tags',
        '--separator',
        '|',
        '--config',
        'devices { use_devicesfile = 0 filter = ["a|.*|"] }',
    ]

    p = commands.start(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out, err = commands.communicate(p)

    if p.returncode != 0:
        raise cmdutils.Error(cmd, p.returncode, out, err)

    result = set()
    for line in out.decode("utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        # vgs prints "vg_name|tag1,tag2,..." with our --separator.
        name, _, tags = line.partition("|")
        name = name.strip()
        if not name:
            continue
        if sc.STORAGE_DOMAIN_TAG in tags.split(","):
            log.debug("Skipping oVirt-tagged VG %r", name)
            continue
        result.add(name)
    return sorted(result)


def _run_vgimportdevices(vg):
    """
    Import underlying devices of provided VG into LVM devices file. Import is
    done using vgimportdevices command. vgimportdevices takes into account
    existing lvm filter, so if some devices are excluded by the filter, such
    devices won't be imported. If the filter is wrong, we may miss some
    devices. To avoid such situation, set the filter to enable all the devices.
    """
    cmd = [
        constants.EXT_LVM,
        'vgimportdevices',
        vg,
        '--config',
        'devices { use_devicesfile = 1 filter = ["a|.*|"] }',
    ]

    p = commands.start(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
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
    cmd = [constants.EXT_LVM, 'lvmdevices', "--check"]

    p = commands.start(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out, err = commands.communicate(p)

    if p.returncode == 0 and err:
        log.warning("Found following issues in LVM devices file: %s", err)

    if p.returncode != 0:
        raise cmdutils.Error(cmd, p.returncode, out, err)
