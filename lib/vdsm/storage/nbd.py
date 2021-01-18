#
# Copyright 2018 Red Hat, Inc.
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
NBD - manage network block devices
"""

from __future__ import absolute_import
from __future__ import division

import collections
import errno
import json
import logging
import os
import time

from vdsm.common import cmdutils
from vdsm.common import constants
from vdsm.common import properties
from vdsm.common import supervdsm
from vdsm.common import systemctl
from vdsm.common import systemd
from vdsm.common import nbdutils
from vdsm.common.time import monotonic_time

from . import constants as sc
from . import exception as se
from . import fileUtils
from . import qemuimg
from . import transientdisk
from . sdc import sdCache

DEFAULT_SOCKET_MODE = 0o660
RUN_DIR = os.path.join(constants.P_VDSM_RUN, "nbd")
OVERLAY = "overlay"

QEMU_NBD = cmdutils.CommandPath(
    "qemu-nbd", "/usr/local/bin/qemu-nbd", "/usr/bin/qemu-nbd")

log = logging.getLogger("storage.nbd")


class Error(Exception):
    """ Base class for nbd errors """


class Timeout(Error):
    """ Timeout starting nbd server """


class InvalidPath(Error):
    """ Path is not a valid volume path """


class ServerConfig(properties.Owner):

    sd_id = properties.UUID(required=True)
    img_id = properties.UUID(required=True)
    vol_id = properties.UUID(required=True)
    readonly = properties.Boolean(default=False)
    discard = properties.Boolean(default=False)
    backing_chain = properties.Boolean(default=True)
    bitmap = properties.UUID()

    def __init__(self, config):
        self.sd_id = config.get("sd_id")
        self.img_id = config.get("img_id")
        self.vol_id = config.get("vol_id")
        self.readonly = config.get("readonly")
        self.discard = config.get("discard")

        # Setting to None overrides the default value.
        # See https://bugzilla.redhat.com/1892403
        self.backing_chain = config.get("backing_chain", True)

        self.bitmap = config.get("bitmap")

        if not self.backing_chain and self.bitmap:
            # When exporting a bitmap we always export the entire chain.
            raise se.UnsupportedOperation(
                "Cannot export bitmap with backing_chain=False")

        if self.bitmap and not self.readonly:
            # Exporting bitmaps makes sense only for incremental backup.
            raise se.UnsupportedOperation(
                "Cannot export bitmap for writing")


QemuNBDConfig = collections.namedtuple(
    "QemuNBDConfig",
    "format,readonly,discard,path,backing_chain,is_block,bitmap")


def start_server(server_id, config):
    cfg = ServerConfig(config)
    dom = sdCache.produce_manifest(cfg.sd_id)
    vol = dom.produceVolume(cfg.img_id, cfg.vol_id)

    if vol.isShared() and not cfg.readonly:
        raise se.SharedVolumeNonWritable(vol)

    if cfg.bitmap and vol.getFormat() != sc.COW_FORMAT:
        raise se.UnsupportedOperation("Cannot export bitmap from RAW volume")

    _create_rundir()

    using_overlay = cfg.bitmap and vol.getParent() != sc.BLANK_UUID

    if using_overlay:
        path = _create_overlay(server_id, vol.volumePath, cfg.bitmap)
        format = "qcow2"
        is_block = False
    else:
        path = vol.volumePath
        format = sc.fmt2str(vol.getFormat())
        is_block = vol.is_block()
    try:
        sock = _socket_path(server_id)

        log.info(
            "Starting transient service %s, serving %s via unix socket %s",
            _service_name(server_id), path, sock)

        qemu_nbd_config = QemuNBDConfig(
            format=format,
            readonly=cfg.readonly,
            discard=cfg.discard,
            path=path,
            backing_chain=cfg.backing_chain,
            is_block=is_block,
            bitmap=cfg.bitmap)

        start_transient_service(server_id, qemu_nbd_config)

        if not _wait_for_socket(sock, 1.0):
            raise Timeout("Timeout starting NBD server {}: {}"
                          .format(server_id, config))
    finally:
        if using_overlay:
            # When qemu-nbd is ready it has an open file descriptor, and it
            # does not need the overlay. Removing the overlay now simplifies
            # cleanup when stopping the service.
            _remove_overlay(server_id)

    os.chmod(sock, DEFAULT_SOCKET_MODE)
    unix_address = nbdutils.UnixAddress(sock)
    return unix_address.url()


def stop_server(server_id):
    service = _service_name(server_id)

    # systemctl.stop() does not have a way to detect that a server was not
    # running, so we need to check the service state before we stop it. This
    # is racy, but should be fine since service names are random and we start
    # them only once.

    info = systemctl.show(service, properties=("LoadState",))
    if info and info[0]["LoadState"] == "not-found":
        log.info("Transient service %s is not running", service)
        return

    log.info("Stopping transient service %s", service)
    systemctl.stop(service)


def _create_overlay(server_id, backing, bitmap):
    """
    To export bitmaps from entire chain, we need to create an overlay, and
    merge all bitmaps from the chain into the overlay.
    """
    filenames = _find_bitmap(backing, bitmap)
    if not filenames:
        raise se.BitmapDoesNotExist(bitmap=bitmap)

    overlay = transientdisk.create_disk(
        server_id,
        OVERLAY,
        backing=backing,
        backing_format="qcow2")["path"]
    try:
        # Merge bitmap from filenames into overlay.
        qemuimg.bitmap_add(overlay, bitmap).run()
        for src_img in filenames:
            qemuimg.bitmap_merge(
                src_img, bitmap, "qcow2", overlay, bitmap).run()
    except:
        try:
            transientdisk.remove_disk(server_id, OVERLAY)
        except Exception:
            log.exception("Error removing overlay: %s", overlay)
        raise

    return overlay


def _remove_overlay(server_id):
    """
    Remove the overlay created by _create_overlay().
    """
    transientdisk.remove_disk(server_id, OVERLAY)


def _find_bitmap(path, bitmap):
    """
    Return filenames in backing chain containing bitmap.

    An example of normal case. "bitmap1" was create after we took a snapshot
    and added "/file2". Then we took another snapshot and added /file3".

    [
      {
        "filename": "/file3"
        "bitmaps":
          [
             {
               "name": "bitmap1",
               "flags": ["auto"]
             }
          ]
      },
      {
        "filename": "/file2"
        "bitmaps":
          [
             {
               "name": "bitmap1",
               "flags": ["auto"]
             }
          ]
      },
      {
        "filename": "/file1"
      }
    ]

    In this case we return the filenames:

      ["/file3", "/file2"]

    The caller can merge the contents of "bitmap1" from these files to create a
    new bitmap referencing all the data written to the disk since "bitmap1" was
    created.

    We have several cases when the bitmap chain is invalid, and cannot be used
    to create incremental backup.

    Case 1: The bitmap is inconsistent ("in-use" flag) in one of the nodes.
    We cannot trust the data in this bitmap.

    [
      {
        "filename": "/file2"
        "bitmaps":
          [
            {
              "name": "bitmap1",
              "flags": ["in-use", "auto"]
            }
          ]
      },
      {
        "filename": "/file1"
        "bitmaps":
          [
            {
              "name": "bitmap1",
              "flags": ["auto"]
            }
          ]
      }
    ]

    Case 2: The bitmap is disabled (no "auto" flag) on one of the nodes. This
    bitmap may be missing data added while the bitmap was disabled.

    [
      {
        "filename": "/file2"
        "bitmaps":
          [
            {
              "name": "bitmap1",
              "flags": ["auto"]
            }
          ]
      },
      {
        "filename": "/file1"
        "bitmaps":
          [
            {
              "name": "bitmap1",
              "flags": []
            }
          ]
      }
    ]

    Case 3: The bitmap is missing in in one node, but exists on the next node.
    Data from the node may be missing from the backup.

    Here the bitmap is missing in the top node:

    [
      {
        "filename": "/file2"
      },
      {
        "filename": "/file1"
        "bitmaps":
          [
            {
              "name": "bitmap1",
              "flags": ["auto"]
            }
          ]
      }
    ]

    Here the bitmap is missing in the middle node:

    [
      {
        "filename": "/file3"
        "bitmaps":
          [
            {
              "name": "bitmap1",
              "flags": ["auto"]
            }
          ]
      },
      {
        "filename": "/file2"
      },
      {
        "filename": "/file1"
        "bitmaps":
          [
            {
              "name": "bitmap1",
              "flags": ["auto"]
            }
          ]
      }
    ]

    Case 4: Bitmap does not exist on any node. This is a caller error, or maybe
    someone deleted the bitmaps manually from the chain.

    [
      {
        "filename": "/file2"
      },
      {
        "filename": "/file1"
      }
    ]

    Raises se.InvalidBitmapChain if bitmap is invalid or missing in one of the
    backing chain nodes.
    """
    filenames = []
    missing = []

    for node in qemuimg.info(path, format="qcow2", backing_chain=True):
        if node["format"] != "qcow2":
            break

        node_bitmaps = node["format-specific"]["data"].get("bitmaps", [])

        for bitmap_info in node_bitmaps:
            if bitmap_info["name"] != bitmap:
                continue

            if "in-use" in bitmap_info["flags"]:
                raise se.InvalidBitmapChain(
                    reason="Bitmap in use",
                    bitmap=bitmap_info,
                    filename=node["filename"])

            if "auto" not in bitmap_info["flags"]:
                raise se.InvalidBitmapChain(
                    reason="Bitmap is disabled",
                    bitmap=bitmap_info,
                    filename=node["filename"])

            if missing:
                # This bitmap was not found in previous nodes - a hole.
                raise se.InvalidBitmapChain(
                    reason="Bitmap is missing in {}".format(missing),
                    bitmap=bitmap)

            # Found a valid bitmap in this node.
            filenames.append(node["filename"])
            break
        else:
            # Bitmap is not in this node. Continue to search next nodes to
            # detect holes.
            missing.append(node["filename"])

    return filenames


def start_transient_service(server_id, config):
    if os.geteuid() != 0:
        return supervdsm.getProxy().nbd_start_transient_service(
            server_id, config)

    _verify_path(config.path)

    cmd = [
        str(QEMU_NBD),
        "--socket", _socket_path(server_id),
        "--persistent",

        # Allow up to 8 clients to share the device. Safe for readers, but for
        # now, consistency is not guaranteed between multiple writers.  Eric
        # Blake says it should be safe if clients write to distinct areas.
        # https://patchwork.kernel.org/patch/11096321/
        "--shared=8",

        # Use empty export name for nicer url: "nbd:unix:/path" instead of
        # "nbd:unix:/path:exportname=name".
        "--export-name=",

        "--cache=none",
        "--aio=native",
    ]

    if config.readonly:
        cmd.append("--read-only")
    elif config.discard:
        cmd.append("--discard=unmap")

    if config.bitmap:
        cmd.append("--bitmap={}".format(config.bitmap))

    cmd.append(json_uri(config))

    systemd.run(
        cmd,
        unit=_service_name(server_id),
        uid=fileUtils.resolveUid(constants.VDSM_USER),
        gid=fileUtils.resolveGid(constants.VDSM_GROUP))


def json_uri(config):
    image = {
        "driver": config.format,
        "file": {
            "driver": "host_device" if config.is_block else "file",
            "filename": config.path,
        }
    }

    if config.format == "qcow2" and not config.backing_chain:
        image["backing"] = None

    return "json:" + json.dumps(image)


def _verify_path(path):
    """
    Anyone running as vdsm can invoke nbd_start_transient_service() with
    arbitrary path. Verify the path is in the storage repository, or path is a
    transient disk with a backing file in the storage repository.
    """
    path = os.path.normpath(path)

    if path.startswith(transientdisk.P_TRANSIENT_DISKS):
        path = qemuimg.info(path, format="qcow2")["backing-filename"]

    if not path.startswith(sc.REPO_MOUNT_DIR):
        raise InvalidPath(
            "Path {!r} is outside storage repository {!r}"
            .format(path, sc.REPO_MOUNT_DIR))


def _service_name(server_id):
    return "vdsm-nbd-{}.service".format(server_id)


def _socket_path(server_id):
    return os.path.join(RUN_DIR, server_id + ".sock")


def _wait_for_socket(sock, timeout):
    start = monotonic_time()
    elapsed = 0.0

    while elapsed < timeout:
        if os.path.exists(sock):
            log.debug("Waited for socket %.3f seconds", elapsed)
            return True
        # Socket is usually availble after 20 milliseconds.
        time.sleep(0.02)
        elapsed = monotonic_time() - start

    return False


def _create_rundir():
    try:
        # /run/vdsm must exists, created by systemd. Do not try to recreate it,
        # hiding the fact that it was missing.
        os.mkdir(RUN_DIR)
    except OSError as e:
        if e.errno != errno.EEXIST:
            raise
    else:
        log.info("Created %s", RUN_DIR)
