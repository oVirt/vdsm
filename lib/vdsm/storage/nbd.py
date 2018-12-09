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

import errno
import logging
import os
import time

from vdsm.common import constants
from vdsm.common import properties
from vdsm.common import supervdsm
from vdsm.common.time import monotonic_time

from . import constants as sc
from . import exception as se
from . sdc import sdCache

QEMU_NBD = "/usr/bin/qemu-nbd"
RUN_DIR = os.path.join(constants.P_VDSM_RUN, "nbd")

log = logging.getLogger("storage.nbd")


class Timeout(Exception):
    pass


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


def start_server(server_id, config):
    cfg = ServerConfig(config)
    dom = sdCache.produce_manifest(cfg.sd_id)
    vol = dom.produceVolume(cfg.img_id, cfg.vol_id)

    if vol.isShared() and not cfg.readonly:
        raise se.SharedVolumeNonWritable(vol)

    cmd = [QEMU_NBD]

    sock = _socket_path(server_id)
    service = _service_name(server_id)

    cmd.append("--socket")
    cmd.append(sock)

    cmd.append("--format")
    cmd.append(sc.fmt2str(vol.getFormat()))

    cmd.append("--persistent")

    # Use empty export name for nicer url: "nbd:unix:/path" instead of
    # "nbd:unix:/path:exportname=name".
    cmd.append("--export-name=")

    cmd.append("--cache=none")
    cmd.append("--aio=native")

    if cfg.readonly:
        cmd.append("--read-only")
    elif cfg.discard:
        cmd.append("--discard=unmap")

    cmd.append(vol.getVolumePath())

    _create_rundir()

    log.info("Starting transient service %s, serving volume %s/%s via unix "
             "socket %s",
             service, cfg.sd_id, cfg.vol_id, sock)

    supervdsm.getProxy().systemd_run(
        cmd,
        unit=service,
        uid=os.getuid(),
        gid=os.getgid())

    if not _wait_for_socket(sock, 1.0):
        raise Timeout("Timeout starting NBD server {}: {}"
                      .format(server_id, config))

    return "nbd:unix:" + sock


def stop_server(server_id):
    service = _service_name(server_id)
    log.info("Stopping transient service %s", service)
    supervdsm.getProxy().systemctl_stop(service)


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
