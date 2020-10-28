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
To run this test you must run the tests as root, or have writable /run/vdsm and
running supervdsm serving the user running the tests.

To setup the environment for unprivileged user:

    $ sudo mkdir /run/vdsm

    $ sudo chown $USER:$USER /run/vdsm

    $ sudo env PYTHONPATH=lib static/usr/sbin/supervdsmd \
          --data-center /var/tmp/vdsm/data-center \
          --sockfile /run/vdsm/svdsm.sock \
          --user=$USER \
          --group=$USER \
          --logger-conf tests/conf/svdsm.logger.conf \
          --disable-gluster \
          --disable-network
"""

from __future__ import absolute_import
from __future__ import division

import os
import stat
import uuid

from contextlib import contextmanager

import pytest

from vdsm.common import cmdutils
from vdsm.common import supervdsm
from vdsm.common.units import KiB, MiB, GiB
from vdsm.storage import constants as sc
from vdsm.storage import exception as se
from vdsm.storage import nbd
from vdsm.storage import qemuimg

from . import qemuio
from . marks import broken_on_ci
from . storagetestlib import fake_env, make_qemu_chain

# TODO: Move to actual code when we support preallocated qcow2 images.
PREALLOCATION = {
    sc.RAW_FORMAT: qemuimg.PREALLOCATION.FALLOC,
    sc.COW_FORMAT: qemuimg.PREALLOCATION.METADATA,
}


def have_supervdsm():
    return os.access(supervdsm.ADDRESS, os.W_OK)


def is_root():
    return os.geteuid() == 0


requires_privileges = pytest.mark.skipif(
    not (is_root() or have_supervdsm()),
    reason="requires root or running supervdsm")


broken_on_ci = broken_on_ci.with_args(
    reason="requires systemd daemon able to run services")


@pytest.fixture
def nbd_env(monkeypatch):
    """
    Fixture for serving a volume using nbd server.
    """
    # These tests require supervdsm running, so we cannot use a random
    # directory. We need to use the same path used to start supervdsm.
    data_center = "/var/tmp/vdsm/data-center"

    with fake_env("file", data_center=data_center) as env:
        # When using XFS, the minimal allocation for qcow2 images is 1MiB. Lets
        # use larger size so we can test properly unallocated areas.
        env.virtual_size = 10 * MiB

        # Source image for copying into the nbd server.
        env.src = os.path.join(env.tmpdir, "src")

        # Destination for copying from nbd server.
        env.dst = os.path.join(env.tmpdir, "dst")

        # Create source image with some data. Using qcow2 format to make it
        # easier to test with different file systems.
        op = qemuimg.create(
            env.src, size=env.virtual_size, format="qcow2", qcow2Compat="1.1")
        op.run()
        qemuio.write_pattern(
            env.src, "qcow2", offset=1 * MiB, len=64 * KiB, pattern=0xf0)
        qemuio.write_pattern(
            env.src, "qcow2", offset=2 * MiB, len=64 * KiB, pattern=0xf1)

        yield env


@broken_on_ci
@requires_privileges
@pytest.mark.parametrize("format", [sc.COW_FORMAT, sc.RAW_FORMAT])
@pytest.mark.parametrize("allocation", [sc.SPARSE_VOL, sc.PREALLOCATED_VOL])
@pytest.mark.parametrize("discard", [True, False])
def test_roundtrip(nbd_env, format, allocation, discard):
    # Volume served by qemu-nd.
    img_id = str(uuid.uuid4())
    vol_id = str(uuid.uuid4())
    nbd_env.make_volume(
        nbd_env.virtual_size,
        img_id,
        vol_id,
        vol_format=format,
        prealloc=allocation)

    # Server configuration.
    config = {
        "sd_id": nbd_env.sd_manifest.sdUUID,
        "img_id": img_id,
        "vol_id": vol_id,
        "discard": discard,
    }

    with nbd_server(config) as nbd_url:
        # Copy data from src to NBD server.
        op = qemuimg.convert(
            nbd_env.src,
            nbd_url,
            srcFormat="qcow2",
            create=False,
            target_is_zero=True)
        op.run()

        # Copy data from NBD server to dst.
        op = qemuimg.convert(
            nbd_url, nbd_env.dst, dstFormat="qcow2", dstQcow2Compat="1.1")
        op.run()

    # Both files should be identical now.
    op = qemuimg.compare(nbd_env.src, nbd_env.dst, strict=True)
    op.run()

    # Now the server should not be accessible.
    with pytest.raises(cmdutils.Error):
        qemuimg.info(nbd_url)


@broken_on_ci
@requires_privileges
@pytest.mark.parametrize("format", [sc.COW_FORMAT, sc.RAW_FORMAT])
@pytest.mark.parametrize("allocation", [sc.SPARSE_VOL, sc.PREALLOCATED_VOL])
def test_readonly(nbd_env, format, allocation):
    # Volume served by qemu-nd.
    img_id = str(uuid.uuid4())
    vol_id = str(uuid.uuid4())
    nbd_env.make_volume(
        nbd_env.virtual_size,
        img_id,
        vol_id,
        vol_format=format,
        prealloc=allocation)

    # Fill volume with data before starting the server.
    vol = nbd_env.sd_manifest.produceVolume(img_id, vol_id)
    op = qemuimg.convert(
        nbd_env.src,
        vol.getVolumePath(),
        srcFormat="qcow2",
        dstFormat=sc.fmt2str(format),
        dstQcow2Compat="1.1",
        preallocation=PREALLOCATION.get(format))
    op.run()

    # Server configuration.
    config = {
        "sd_id": nbd_env.sd_manifest.sdUUID,
        "img_id": img_id,
        "vol_id": vol_id,
        "readonly": True,
    }

    with nbd_server(config) as nbd_url:
        # Writing to NBD server must fail.
        with pytest.raises(cmdutils.Error):
            op = qemuimg.convert(
                nbd_env.src,
                nbd_url,
                srcFormat="qcow2",
                create=False,
                target_is_zero=True)
            op.run()

        # Copy data from NBD server to dst. Both files should match byte
        # for byte after the operation.
        op = qemuimg.convert(
            nbd_url, nbd_env.dst, dstFormat="qcow2", dstQcow2Compat="1.1")
        op.run()

    op = qemuimg.compare(nbd_env.src, nbd_env.dst, strict=True)
    op.run()

    # Now the server should not be accessible.
    with pytest.raises(cmdutils.Error):
        qemuimg.info(nbd_url)


@broken_on_ci
@requires_privileges
@pytest.mark.parametrize("backing_chain", [
    pytest.param(True, id="true"),
    pytest.param(None, id="default"),
])
def test_backing_chain(nbd_env, backing_chain):
    base, top = make_qemu_chain(
        nbd_env, nbd_env.virtual_size, sc.COW_FORMAT, 2, qcow2_compat='1.1')

    # Fill volumes with data.
    qemuio.write_pattern(
        base.volumePath, "qcow2", offset=1 * MiB, len=64 * KiB, pattern=0xf0)
    qemuio.write_pattern(
        top.volumePath, "qcow2", offset=2 * MiB, len=64 * KiB, pattern=0xf1)

    # Server configuration.
    config = {
        "sd_id": top.sdUUID,
        "img_id": top.imgUUID,
        "vol_id": top.volUUID,
        "readonly": True,
    }

    if backing_chain is not None:
        config["backing_chain"] = backing_chain

    # Copy data from NBD server to dst.
    with nbd_server(config) as nbd_url:
        op = qemuimg.convert(
            nbd_url, nbd_env.dst, dstFormat="qcow2", dstQcow2Compat="1.1")
        op.run()

    # Compare copied data to source chain.
    op = qemuimg.compare(top.volumePath, nbd_env.dst, strict=True)
    op.run()


@broken_on_ci
@requires_privileges
def test_no_backing_chain(nbd_env):
    base, top = make_qemu_chain(
        nbd_env, nbd_env.virtual_size, sc.COW_FORMAT, 2, qcow2_compat='1.1')

    # Fill volumes with data.
    qemuio.write_pattern(
        base.volumePath, "qcow2", offset=1 * MiB, len=64 * KiB, pattern=0xf0)
    qemuio.write_pattern(
        top.volumePath, "qcow2", offset=2 * MiB, len=64 * KiB, pattern=0xf1)

    # Download base volume.

    config = {
        "sd_id": base.sdUUID,
        "img_id": base.imgUUID,
        "vol_id": base.volUUID,
        "readonly": True,
        "backing_chain": False,
    }

    with nbd_server(config) as nbd_url:
        op = qemuimg.convert(
            nbd_url, nbd_env.dst, dstFormat="qcow2", dstQcow2Compat="1.1")
        op.run()

    op = qemuimg.compare(base.volumePath, nbd_env.dst, strict=True)
    op.run()

    # Download top volume.

    config = {
        "sd_id": top.sdUUID,
        "img_id": top.imgUUID,
        "vol_id": top.volUUID,
        "readonly": True,
        "backing_chain": False,
    }

    with nbd_server(config) as nbd_url:
        op = qemuimg.convert(
            nbd_url, nbd_env.dst, dstFormat="qcow2", dstQcow2Compat="1.1")
        op.run()

    # Remove backing file from top volume, so we can compare to top.
    op = qemuimg.rebase(top.volumePath, "", unsafe=True)
    op.run()

    op = qemuimg.compare(top.volumePath, nbd_env.dst, strict=True)
    op.run()


@broken_on_ci
@requires_privileges
def test_server_socket_mode(nbd_env):
    # Volume served by qemu-nd.
    img_id = str(uuid.uuid4())
    vol_id = str(uuid.uuid4())
    nbd_env.make_volume(
        nbd_env.virtual_size,
        img_id,
        vol_id,
        vol_format=sc.COW_FORMAT,
        prealloc=sc.SPARSE_VOL)

    # Server configuration.
    config = {
        "sd_id": nbd_env.sd_manifest.sdUUID,
        "img_id": img_id,
        "vol_id": vol_id,
    }

    with nbd_server(config) as nbd_url:
        # Remove "nbd:unix:" from nbd_url.
        socket = nbd_url[9:]

        actual_mode = stat.S_IMODE(os.stat(socket).st_mode)
        assert oct(actual_mode) == oct(0o660)


def test_shared_volume():
    with fake_env("file") as env:
        img_id = str(uuid.uuid4())
        vol_id = str(uuid.uuid4())
        env.make_volume(GiB, img_id, vol_id)
        vol = env.sd_manifest.produceVolume(img_id, vol_id)
        vol.setShared()

        config = {
            "sd_id": env.sd_manifest.sdUUID,
            "img_id": img_id,
            "vol_id": vol_id,
        }

        with pytest.raises(se.SharedVolumeNonWritable):
            nbd.start_server("no-server", config)


@broken_on_ci
def test_stop_server_not_running():
    # Stopping non-existing server should succeed.
    nbd.stop_server("no-such-server-uuid")


@contextmanager
def nbd_server(config):
    server_id = str(uuid.uuid4())
    nbd_url = nbd.start_server(server_id, config)
    try:
        yield nbd_url
    finally:
        nbd.stop_server(server_id)
