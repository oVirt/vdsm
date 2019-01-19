#
# Copyright 2010-2019 Red Hat, Inc.
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

This module provides needed interfaces to for attaching and detaching volumes:
    - connector_info() - returns connector info
    - attach_volume() - attach a volume according to the connection info
                        provided
    - detach_volume() - detach a volume according to the device info provided
"""

from __future__ import absolute_import
from __future__ import division

import json
import logging
import os
from contextlib import closing

# TODO: Change to simple import when os_brick is available
#       and required
try:
    import os_brick
except ImportError:
    os_brick = None

from vdsm.common import cmdutils
from vdsm.common import commands
from vdsm.storage import managedvolumedb
from vdsm.common import supervdsm
from vdsm.storage import exception as se

HELPER = '/usr/libexec/vdsm/managedvolume-helper'
DEV_MAPPER = "/dev/mapper"
DEV_RBD = "/dev/rbd"

log = logging.getLogger("storage.managedvolume")


# Public interface


def connector_info():
    """
    Get connector information from os-brick.
        If not running as root, use supervdsm to invoke this function as root.
    """
    if os_brick is None:
        raise se.ManagedVolumeNotSupported("Cannot import os_brick.initiator")

    log.debug("Starting get connector_info")
    return run_helper("connector_info")


def attach_volume(vol_id, connection_info):
    """
    Attach volume with os-brick.
    """
    if os_brick is None:
        raise se.ManagedVolumeNotSupported("Cannot import os_brick.initiator")

    db = managedvolumedb.open()
    with closing(db):
        try:
            vol_info = db.get_volume(vol_id)
        except managedvolumedb.NotFound:
            db.add_volume(vol_id, connection_info)
        else:
            if vol_info["connection_info"] != connection_info:
                raise se.ManagedVolumeConnectionMismatch(
                    vol_id, vol_info["connection_info"], connection_info)

            if "path" in vol_info and os.path.exists(vol_info["path"]):
                raise se.ManagedVolumeAlreadyAttached(
                    vol_id, vol_info["path"], vol_info.get('attachment'))

        log.debug("Starting Attach volume with os-brick")

        try:
            attachment = run_helper("attach", connection_info)
            path = _resolve_path(vol_id, connection_info, attachment)
            try:
                db.update_volume(
                    vol_id,
                    path=path,
                    attachment=attachment,
                    multipath_id=attachment.get("multipath_id"))
            except:
                vol_info = {"connection_info": connection_info,
                            "attachment": attachment}
                run_helper("detach", vol_info)
                raise
        except:
            try:
                db.remove_volume(vol_id)
            except Exception:
                log.exception("Failed to remove managed volume from DB")
            raise

    log.debug("Attached volume: %s", attachment)

    ret = {'attachment': attachment, 'path': path}
    return {'result': ret}


def detach_volume(vol_id):
    """
    Detach volume with os-brick.
    """
    if os_brick is None:
        raise se.ManagedVolumeNotSupported("Cannot import os_brick.initiator")

    db = managedvolumedb.open()
    with closing(db):
        try:
            vol_info = db.get_volume(vol_id)
        except managedvolumedb.NotFound:
            return

        log.debug("Starting Detach volume with os-brick")
        if "path" in vol_info and os.path.exists(vol_info["path"]):
            run_helper("detach", vol_info)

        db.remove_volume(vol_id)


# supervdsm interface


def run_helper(sub_cmd, cmd_input=None):
    if os.geteuid() != 0:
        return supervdsm.getProxy().managedvolume_run_helper(
            sub_cmd, cmd_input=cmd_input)
    try:
        if cmd_input:
            cmd_input = json.dumps(cmd_input).encode("utf-8")
        result = commands.run([HELPER, sub_cmd], input=cmd_input)
    except cmdutils.Error as e:
        raise se.ManagedVolumeHelperFailed("Error executing helper: %s" % e)
    try:
        return json.loads(result)
    except ValueError as e:
        raise se.ManagedVolumeHelperFailed("Error loading result: %s" % e)


# Private helpers


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
