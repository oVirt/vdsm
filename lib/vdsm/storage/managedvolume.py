# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

"""

This module provides needed interfaces to for attaching and detaching volumes:
    - connector_info() - returns connector info
    - attach_volume() - attach a volume according to the connection info
                        provided
    - detach_volume() - detach a volume according to the device info provided
"""

from __future__ import absolute_import
from __future__ import division

import functools
import json
import logging
import os
import sys

from contextlib import closing

# TODO: Change to simple import when os_brick is available
#       and required
try:
    import os_brick
except ImportError:
    os_brick = None

from vdsm.common import cmdutils
from vdsm.common import commands
from vdsm.common import supervdsm

from vdsm.storage import exception as se
from vdsm.storage import lvm
from vdsm.storage import managedvolumedb

HELPER = '/usr/libexec/vdsm/managedvolume-helper'
DEV_MAPPER = "/dev/mapper"
DEV_RBD = "/dev/rbd"
VOLUME_LINK_DIR = "/run/vdsm/managedvolumes/"

# Drivers that have been tested and are known to work.
SUPPORTED_DRIVERS = (
    # Drivers tested by the oVirt team
    "rbd",
    "iscsi",

    # Tested by Muli Ben-Yehuda <info (at) lightbitslabs.com>
    "lightos",

    # Tested by Moritz Wanzenböck <technik+ovirt (at) linbit.com>
    "local",
)

log = logging.getLogger("storage.managedvolume")


def requires_os_brick(func):

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        if os_brick is None:
            raise se.ManagedVolumeNotSupported("Cannot import os_brick")
        return func(*args, **kwargs)

    return wrapper


# Public interface


@requires_os_brick
def connector_info():
    """
    Get connector information from os-brick.
        If not running as root, use supervdsm to invoke this function as root.
    """
    log.debug("Starting get connector_info")
    return run_helper("connector_info")


@requires_os_brick
def attach_volume(sd_id, vol_id, connection_info):
    """
    Attach volume with os-brick.
    """
    db = managedvolumedb.open()
    with closing(db):
        _add_volume(db, vol_id, connection_info)

        log.debug("Starting attach volume %s connection_info=%s",
                  vol_id, connection_info)

        try:
            attachment = run_helper("attach", connection_info, connection_info.get("adapter"))
            try:
                path = _resolve_path(vol_id, connection_info, attachment)
                db.update_volume(
                    vol_id,
                    path=path,
                    attachment=attachment,
                    multipath_id=attachment.get("multipath_id"))
                _invalidate_lvm_devices(attachment)
                volume_type = connection_info["driver_volume_type"]
                if volume_type not in SUPPORTED_DRIVERS:
                    raise se.UnsupportedOperation(
                        "Unsupported volume type, supported types are: "
                        f"{SUPPORTED_DRIVERS}")

                run_link = _add_run_link(sd_id, vol_id, path)
                _add_udev_rule(sd_id, vol_id, path)
            except:
                _silent_detach(connection_info, attachment)
                raise
        except:
            _silent_remove(db, sd_id, vol_id)
            raise
    log.debug("Attached volume %s attachment=%s", vol_id, attachment)

    return {"result": {'attachment': attachment, 'path': path,
                       'vol_id': vol_id, 'managed_path': run_link}}


@requires_os_brick
def detach_volume(sd_id, vol_id):
    """
    Detach volume with os-brick.
    """
    db = managedvolumedb.open()
    with closing(db):
        try:
            vol_info = db.get_volume(vol_id)
        except managedvolumedb.NotFound:
            return

        log.debug("Starting detach volume %s vol_info=%s", vol_id, vol_info)

        adapter = None
        if connection_info := vol_info.get("connection_info"):
            adapter = connection_info.get("adapter")

        if "path" in vol_info and os.path.exists(vol_info["path"]):
            run_helper("detach", vol_info, adapter)

        _remove_udev_rule(sd_id, vol_info['vol_id'])
        _remove_run_link(sd_id, vol_id)
        db.remove_volume(vol_id)


def volumes_info(vol_ids=()):
    """
    Lookup volumes information in managed volume database.

    Lookup volumes info in managed volume database for all volume IDs in the
    vol_ids list and returns a list with volume information for each volume ID
    which is present in the database. Each record contains connection info.
    Path and attachment info of the volume is contained only when the resource
    is attached. Dictionary can also contain 'exists' item, which is set to
    True if the volume is connected and to False otherwise. Empty list is
    returned if any of IDs are not in the database.

    If the list of requested volume IDs is not specified or empty, list of all
    volumes info in the DB is returned.

    Arguments:
            vol_ids (list): list of queried volume IDs.

    Returns:
            List of managed volumes information.
    """
    db = managedvolumedb.open()
    with closing(db):
        result = []
        for vol_info in db.iter_volumes(vol_ids):
            if "path" in vol_info:
                vol_info["exists"] = os.path.exists(vol_info["path"])
            vol_info.pop("multipath_id", None)
            result.append(vol_info)

    return {"result": result}


# supervdsm interface


def run_helper(sub_cmd, cmd_input=None, adapter=None):
    if os.geteuid() != 0:
        return supervdsm.getProxy().managedvolume_run_helper(
            sub_cmd, cmd_input=cmd_input, adapter=adapter)
    try:
        if cmd_input:
            cmd_input = json.dumps(cmd_input).encode("utf-8")
        helper = HELPER
        if adapter:
            helper = f"{HELPER}-{adapter}"
            if not os.path.exists(helper):
                raise se.ManagedVolumeHelperFailed(
                    f"Helper for adapter '{adapter}' not found"
                    f" at '{helper}'")
        # This is the only sane way to run python scripts that work with both
        # python2 and python3 in the tests.
        # TODO: Remove when we drop python 2.
        cmd = [sys.executable, helper, sub_cmd]
        result = commands.run(cmd, input=cmd_input)
    except cmdutils.Error as e:
        raise se.ManagedVolumeHelperFailed("Error executing helper: %s" % e)
    try:
        return json.loads(result)
    except ValueError as e:
        raise se.ManagedVolumeHelperFailed("Error loading result: %s" % e)


# Private helpers


def _add_volume(db, vol_id, connection_info):
    """
    Add volume to db, verifing existing entry.
    """
    try:
        db.add_volume(vol_id, connection_info)
    except managedvolumedb.VolumeAlreadyExists:
        vol_info = db.get_volume(vol_id)
        if vol_info["connection_info"] != connection_info:
            raise se.ManagedVolumeConnectionMismatch(
                vol_id, vol_info["connection_info"], connection_info)

        if "path" in vol_info and os.path.exists(vol_info["path"]):
            raise se.ManagedVolumeAlreadyAttached(
                vol_id, vol_info["path"], vol_info.get('attachment'))


def _resolve_path(vol_id, connection_info, attachment):
    """
    Resolve the path in attached volume.
    """
    vol_type = connection_info['driver_volume_type']
    if vol_type in ("iscsi", "fibre_channel"):
        if "multipath_id" not in attachment:
            raise se.ManagedVolumeUnsupportedDevice(vol_id, attachment)
        # /dev/mapper/xxxyyy
        return os.path.join(DEV_MAPPER, attachment["multipath_id"])
    elif vol_type == "rbd":
        # /dev/rbd/poolname/volume-vol-id
        return os.path.join(DEV_RBD, connection_info['data']['name'])
    else:
        log.warning("Managed Volume without multipath info: %s",
                    attachment)
        return attachment["path"]


def _invalidate_lvm_devices(attachment):
    """
    Invalidate lvm devices when attached disk has a multipath id.

    Vdsm may discover a managed volume after we connected the device to the
    host on the storage side (FC), or after we attached the volume but before
    we store the multipath id (iSCSI). In this case lvm devices may include the
    device, and the next lvm command will scan the device.

    Invalidate the lvm devices to ensure that it does not contain the managed
    volume.
    """
    if "multipath_id" in attachment:
        lvm.invalidate_devices()


def _silent_remove(db, sd_id, vol_id):
    """
    Remove volume from db, udev rule and link during cleanup flow, logging
    errors.
    """
    try:
        db.remove_volume(vol_id)
    except Exception:
        log.exception("Failed to remove managed volume %s from DB", vol_id)

    _remove_udev_rule(sd_id, vol_id)
    _remove_run_link(sd_id, vol_id)


def _silent_detach(connection_info, attachment):
    """
    Detach volume during cleanup flow, logging errors.
    """
    vol_info = {"connection_info": connection_info,
                "attachment": attachment}
    try:
        run_helper("detach", vol_info)
    except Exception:
        log.exception("Failed to detach managed volume %s", vol_info)


def _add_udev_rule(sd_id, vol_id, path):
    proxy = supervdsm.getProxy()
    proxy.add_managed_udev_rule(sd_id, vol_id, path)
    try:
        proxy.trigger_managed_udev_rule(path)
    except:
        _remove_udev_rule(sd_id, vol_id)
        raise


def _remove_udev_rule(sd_id, vol_id):
    try:
        proxy = supervdsm.getProxy()
        proxy.remove_managed_udev_rule(sd_id, vol_id)
    except Exception:
        log.exception(
            "Failed to remove udev rule for volume %s", vol_id)


def _add_run_link(sd_id, vol_id, path):
    _create_run_dir()
    run_path = _run_link(sd_id, vol_id)
    os.symlink(path, run_path)
    return run_path


def _remove_run_link(sd_id, vol_id):
    try:
        os.remove(_run_link(sd_id, vol_id))
    except Exception:
        log.exception("Failed to remove run link for volume %s", vol_id)


def _create_run_dir():
    try:
        os.mkdir(VOLUME_LINK_DIR)
    except FileExistsError:
        pass


def _run_link(sd_id, vol_id):
    return os.path.join(VOLUME_LINK_DIR, f"{sd_id}_{vol_id}")
