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
from . sdc import sdCache

DEFAULT_SOCKET_MODE = 0o660
RUN_DIR = os.path.join(constants.P_VDSM_RUN, "nbd")

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

    def __init__(self, config):
        self.sd_id = config.get("sd_id")
        self.img_id = config.get("img_id")
        self.vol_id = config.get("vol_id")
        self.readonly = config.get("readonly")
        self.discard = config.get("discard")


QemuNBDConfig = collections.namedtuple(
    "QemuNBDConfig", "format,readonly,discard,path")


def start_server(server_id, config):
    cfg = ServerConfig(config)
    dom = sdCache.produce_manifest(cfg.sd_id)
    vol = dom.produceVolume(cfg.img_id, cfg.vol_id)

    if vol.isShared() and not cfg.readonly:
        raise se.SharedVolumeNonWritable(vol)

    _create_rundir()

    sock = _socket_path(server_id)

    log.info("Starting transient service %s, serving volume %s/%s via unix "
             "socket %s",
             _service_name(server_id), cfg.sd_id, cfg.vol_id, sock)

    qemu_nbd_config = QemuNBDConfig(
        format=sc.fmt2str(vol.getFormat()),
        readonly=cfg.readonly,
        discard=cfg.discard,
        path=vol.getVolumePath())

    start_transient_service(server_id, qemu_nbd_config)

    if not _wait_for_socket(sock, 1.0):
        raise Timeout("Timeout starting NBD server {}: {}"
                      .format(server_id, config))

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


def start_transient_service(server_id, config):
    if os.geteuid() != 0:
        return supervdsm.getProxy().nbd_start_transient_service(
            server_id, config)

    _verify_path(config.path)

    cmd = [
        str(QEMU_NBD),
        "--socket", _socket_path(server_id),
        "--format", config.format,
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

    cmd.append(config.path)

    systemd.run(
        cmd,
        unit=_service_name(server_id),
        uid=fileUtils.resolveUid(constants.VDSM_USER),
        gid=fileUtils.resolveGid(constants.VDSM_GROUP))


def _verify_path(path):
    """
    Anyone running as vdsm can invoke nbd_start_transient_service() with
    arbitrary path. Verify the path is in the storage repository.
    """
    norm_path = os.path.normpath(path)

    if not norm_path.startswith(sc.REPO_MOUNT_DIR):
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
