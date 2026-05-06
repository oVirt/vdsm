# SPDX-FileCopyrightText: Red Hat, Inc.
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
    Populate the LVM devices file from currently-visible VGs and the
    `vgs` argument.

    Imports the VGs received in the `vgs` argument -- typically
    populated by `lvmfilter.find_lvm_mounts`, i.e. the VGs that back
    the host's mounted filesystems (the OS root VG if root is on
    LVM; empty if root is on a partition) -- plus any other VGs
    already visible to lvm at the moment vdsm runs. Without this,
    VGs created by an external layer (e.g. LINSTOR's drbdpool, or
    any operator-deployed VG) while use_devicesfile=0 -- or auto-
    imported under use_devicesfile=1 on a host where vdsm is the
    first agent to populate the file -- are silently evicted from
    the devices file when vdsm rewrites it or first-creates it.

    Operator philosophy: storage intended for oVirt should be blank
    before being attached. Anything already on the host belongs to
    whoever put it there, and vdsm has no signal to override that
    ownership. Strictly safer than today's empty-overwrite behaviour.
    """
    devices_file = _get_devices_file_path()
    file_was_populated = (
        os.path.exists(devices_file) and os.path.getsize(devices_file) > 0
    )

    if not file_was_populated:
        # File doesn't exist or holds only an empty header. List every
        # VG lvm currently sees so external-storage VGs are not evicted
        # on the use_devicesfile=1 transition.
        existing_vgs = _list_all_visible_vgs()
        if not existing_vgs and not vgs:
            # No VGs anywhere on the host. Drop a minimal header file
            # so the PR #324 invariant ("file exists when
            # use_devicesfile=1") still holds; otherwise lvm sees all
            # devices on a host that just wanted the gate flipped on.
            now = datetime.datetime.now()
            data = (f"# Created by Vdsm pid {os.getpid()} at "
                    f"{now.strftime('%a %b %d %H:%M:%S %Y')}\n")
            os.makedirs(os.path.dirname(devices_file), exist_ok=True)
            fileUtils.atomic_write(devices_file, data.encode("utf-8"))
            return

        for vg in existing_vgs:
            _run_vgimportdevices(vg)

    # vgimportdevices is additive; existing entries are not touched.
    for vg in vgs:
        _run_vgimportdevices(vg)


def _list_all_visible_vgs():
    """
    Enumerate VGs lvm can currently see, bypassing the devices file
    so VGs that exist on disk but are not yet recorded in the file
    (because use_devicesfile was off when they were created) are
    surfaced. Returns a sorted list of VG names; empty list if lvm
    sees no VGs.
    """
    cmd = [constants.EXT_LVM, 'vgs',
           '--noheadings', '-o', 'vg_name',
           '--config',
           'devices { use_devicesfile = 0 filter = ["a|.*|"] }']

    p = commands.start(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )
    out, err = commands.communicate(p)

    if p.returncode != 0:
        raise cmdutils.Error(cmd, p.returncode, out, err)

    return sorted({
        name.strip()
        for name in out.decode("utf-8").splitlines()
        if name.strip()
    })


def _run_vgimportdevices(vg):
    """
    Import underlying devices of provided VG into LVM devices file. Import is
    done using vgimportdevices command. vgimportdevices takes into account
    existing lvm filter, so if some devices are excluded by the filter, such
    devices won't be imported. If the filter is wrong, we may miss some
    devices. To avoid such situation, set the filter to enable all the devices.
    """
    cmd = [constants.EXT_LVM,
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
    cmd = [constants.EXT_LVM, 'lvmdevices', "--check"]

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
