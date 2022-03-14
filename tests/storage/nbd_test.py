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

    $ sudo env PYTHONPATH=lib static/libexec/vdsm/supervdsmd \
          --data-center /var/tmp/vdsm/data-center \
          --transient-disks /var/tmp/vdsm/transient-disks \
          --sockfile /run/vdsm/svdsm.sock \
          --user=$USER \
          --group=$USER \
          --logger-conf tests/conf/svdsm.logger.conf \
          --disable-gluster \
          --disable-network
"""

from __future__ import absolute_import
from __future__ import division

import logging
import os
import stat
import uuid

from urllib.parse import urlparse
from contextlib import contextmanager

import pytest

# TODO: Use public API when available.
from ovirt_imageio._internal import nbd as nbd_client

from vdsm.common import cmdutils
from vdsm.common import supervdsm
from vdsm.common.units import KiB, MiB
from vdsm.storage import constants as sc
from vdsm.storage import exception as se
from vdsm.storage import nbd
from vdsm.storage import qemuimg
from vdsm.storage import transientdisk

from . import qemuio
from . marks import broken_on_ci
from . storagetestlib import fake_env, make_qemu_chain

# TODO: Move to actual code when we support preallocated qcow2 images.
PREALLOCATION = {
    "raw": qemuimg.PREALLOCATION.FALLOC,
    "qcow2": qemuimg.PREALLOCATION.METADATA,
}

log = logging.getLogger("test")


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
    # directory. We need to use the same locations used to start supervdsm.

    data_center = "/var/tmp/vdsm/data-center"

    monkeypatch.setattr(
        transientdisk, "P_TRANSIENT_DISKS", "/var/tmp/vdsm/transient-disks")

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
@pytest.mark.parametrize("format", ["qcow2", "raw"])
@pytest.mark.parametrize("allocation", ["sparse", "preallocated"])
def test_roundtrip(nbd_env, format, allocation):
    vol = create_volume(nbd_env, format, allocation)

    config = {
        "sd_id": vol.sdUUID,
        "img_id": vol.imgUUID,
        "vol_id": vol.volUUID,
    }

    with nbd_server(config) as nbd_url:
        upload_to_nbd(nbd_env.src, nbd_url)
        download_from_nbd(nbd_url, nbd_env.dst)

    compare_images(nbd_env.src, nbd_env.dst, strict=True)

    # Now the server should not be accessible.
    with pytest.raises(cmdutils.Error):
        qemuimg.info(nbd_url)


@broken_on_ci
@requires_privileges
@pytest.mark.parametrize("format", ["qcow2", "raw"])
def test_detect_zeroes_discard(nbd_env, format):
    vol = create_volume(nbd_env, format, "sparse")

    config = {
        "sd_id": vol.sdUUID,
        "img_id": vol.imgUUID,
        "vol_id": vol.volUUID,
        "discard": True,
        "detect_zeroes": True,
    }

    with nbd_server(config) as nbd_url:
        # Fill image with zeroes. The writes should be converted to write
        # zeroes command.
        with nbd_client.open(urlparse(nbd_url)) as c:
            c.write(0 * MiB, b"\0" * nbd_env.virtual_size)
            c.flush()
            extents = c.extents(0, nbd_env.virtual_size)

    image_info = qemuimg.info(vol.volumePath)
    if format == "raw":
        assert image_info["actual-size"] == 0
    else:
        # Image contains only metadata.
        assert image_info["actual-size"] <= MiB

    log.debug("image extents: %s", extents)

    # Entire image should be zero extent. For qcow2 image, qemu-nbd writes zero
    # clusters. For raw image, qemu-nbd deallocates the entire image.
    base_alloc = [(e.length, e.zero)
                  for e in extents["base:allocation"]]
    assert base_alloc == [(nbd_env.virtual_size, True)]

    if format != "raw":
        # Entire image should be allocated. For qcow2 image, zero
        # clusters are always allocated.
        alloc_depth = [(e.length, e.hole)
                       for e in extents["qemu:allocation-depth"]]
        assert alloc_depth == [(nbd_env.virtual_size, False)]


@broken_on_ci
@requires_privileges
@pytest.mark.parametrize("format", ["qcow2", "raw"])
def test_detect_zeroes_no_discard(nbd_env, format):
    vol = create_volume(nbd_env, format, "sparse")

    config = {
        "sd_id": vol.sdUUID,
        "img_id": vol.imgUUID,
        "vol_id": vol.volUUID,
        "discard": False,
        "detect_zeroes": True,
    }

    with nbd_server(config) as nbd_url:
        # Fill image with zeroes. The writes should be converted to write
        # zeroes command.
        with nbd_client.open(urlparse(nbd_url)) as c:
            c.write(0 * MiB, b"\0" * nbd_env.virtual_size)
            c.flush()
            extents = c.extents(0, nbd_env.virtual_size)

    # For qcow2 format, in qemu-nd < 6.2 actual zeroes written to image.
    # In 6.2 zeroes seem to be converted to zero clusters. Since the
    # behavior is not consistent we don't have easy way to verify.
    if format == "raw":
        image_info = qemuimg.info(vol.volumePath)
        assert image_info["actual-size"] == nbd_env.virtual_size

    log.debug("image extents: %s", extents)

    # Entire image should be zero extent.
    base_alloc = [(e.length, e.zero)
                  for e in extents["base:allocation"]]
    assert base_alloc == [(nbd_env.virtual_size, True)]

    if format != "raw":
        # qemu-nbd allocated entire image in both cases.
        alloc_depth = [(e.length, e.hole)
                       for e in extents["qemu:allocation-depth"]]
        assert alloc_depth == [(nbd_env.virtual_size, False)]


@broken_on_ci
@requires_privileges
@pytest.mark.parametrize("format", ["qcow2", "raw"])
def test_detect_zeroes_disabled(nbd_env, format):
    vol = create_volume(nbd_env, format, "sparse")

    config = {
        "sd_id": vol.sdUUID,
        "img_id": vol.imgUUID,
        "vol_id": vol.volUUID,
    }

    with nbd_server(config) as nbd_url:
        # Fill image with zeroes.
        with nbd_client.open(urlparse(nbd_url)) as c:
            c.write(0 * MiB, b"\0" * nbd_env.virtual_size)
            c.flush()
            extents = c.extents(0, nbd_env.virtual_size)

    image_info = qemuimg.info(vol.volumePath)
    if format == "raw":
        assert image_info["actual-size"] == nbd_env.virtual_size
    else:
        assert image_info["actual-size"] >= nbd_env.virtual_size

    log.debug("image extents: %s", extents)

    # Entire image should be data extent.
    base_alloc = [(e.length, e.zero)
                  for e in extents["base:allocation"]]
    assert base_alloc == [(nbd_env.virtual_size, False)]

    if format != "raw":
        # Entire image should be allocated.
        alloc_depth = [(e.length, e.hole)
                       for e in extents["qemu:allocation-depth"]]
        assert alloc_depth == [(nbd_env.virtual_size, False)]


@broken_on_ci
@requires_privileges
@pytest.mark.parametrize("format", ["qcow2", "raw"])
@pytest.mark.parametrize("allocation", ["sparse", "preallocated"])
def test_readonly(nbd_env, format, allocation):
    vol = create_volume(nbd_env, format, allocation)

    op = qemuimg.convert(
        nbd_env.src,
        vol.getVolumePath(),
        srcFormat="qcow2",
        dstFormat=format,
        dstQcow2Compat="1.1",
        preallocation=PREALLOCATION.get(format))
    op.run()

    config = {
        "sd_id": vol.sdUUID,
        "img_id": vol.imgUUID,
        "vol_id": vol.volUUID,
        "readonly": True,
    }

    with nbd_server(config) as nbd_url:
        # Writing to read-only NBD server must fail.
        with pytest.raises(cmdutils.Error):
            upload_to_nbd(nbd_env.src, nbd_url)

        # Download must not fail.
        download_from_nbd(nbd_url, nbd_env.dst)

    compare_images(nbd_env.src, nbd_env.dst, strict=True)

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

    config = {
        "sd_id": top.sdUUID,
        "img_id": top.imgUUID,
        "vol_id": top.volUUID,
        "readonly": True,
    }

    if backing_chain is not None:
        config["backing_chain"] = backing_chain

    with nbd_server(config) as nbd_url:
        download_from_nbd(nbd_url, nbd_env.dst)

    compare_images(top.volumePath, nbd_env.dst, strict=True)


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
        download_from_nbd(nbd_url, nbd_env.dst)

    compare_images(base.volumePath, nbd_env.dst, strict=True)

    # Download top volume.

    config = {
        "sd_id": top.sdUUID,
        "img_id": top.imgUUID,
        "vol_id": top.volUUID,
        "readonly": True,
        "backing_chain": False,
    }

    with nbd_server(config) as nbd_url:
        download_from_nbd(nbd_url, nbd_env.dst)

    # Remove backing file from top volume, so we can compare to top.
    op = qemuimg.rebase(top.volumePath, "", unsafe=True)
    op.run()

    compare_images(top.volumePath, nbd_env.dst, strict=True)


@broken_on_ci
@requires_privileges
@pytest.mark.parametrize("format", ["qcow2", "raw"])
@pytest.mark.parametrize("backing_chain", [
    pytest.param(True, id="backing_chain"),
    pytest.param(False, id="no_backing_chain"),
])
def test_allocation_depth(nbd_env, format, backing_chain):
    # Check that qemu-nbd exposes the "qemu:allocation-depth" meta
    # context for qcow2 format.
    vol = create_volume(nbd_env, format, "sparse")
    config = {
        "sd_id": vol.sdUUID,
        "img_id": vol.imgUUID,
        "vol_id": vol.volUUID,
        "readonly": True,
        "backing_chain": backing_chain,
    }
    with nbd_server(config) as nbd_url:
        with nbd_client.open(urlparse(nbd_url)) as c:
            extents = c.extents(0, nbd_env.virtual_size)
            log.debug("extents: %s", extents)
            assert ("qemu:allocation-depth" in extents) == (format != "raw")


@broken_on_ci
@requires_privileges
def test_bitmap_requires_entire_chain(nbd_env):
    vol = create_volume(nbd_env, "qcow2", "sparse")
    invalid_config = {
        "sd_id": vol.sdUUID,
        "img_id": vol.imgUUID,
        "vol_id": vol.volUUID,
        "readonly": True,
        "bitmap": str(uuid.uuid4()),
        "backing_chain": False,
    }
    with pytest.raises(se.UnsupportedOperation):
        with nbd_server(invalid_config):
            pass


@broken_on_ci
@requires_privileges
def test_bitmap_requires_qcow2(nbd_env):
    vol = create_volume(nbd_env, "raw", "sparse")
    invalid_config = {
        "sd_id": vol.sdUUID,
        "img_id": vol.imgUUID,
        "vol_id": vol.volUUID,
        "readonly": True,
        "bitmap": str(uuid.uuid4()),
    }
    with pytest.raises(se.UnsupportedOperation):
        with nbd_server(invalid_config):
            pass


@broken_on_ci
@requires_privileges
def test_bitmap_requires_read_only(nbd_env):
    vol = create_volume(nbd_env, "qcow2", "sparse")
    invalid_config = {
        "sd_id": vol.sdUUID,
        "img_id": vol.imgUUID,
        "vol_id": vol.volUUID,
        "readonly": False,
        "bitmap": str(uuid.uuid4()),
    }
    with pytest.raises(se.UnsupportedOperation):
        with nbd_server(invalid_config):
            pass


@broken_on_ci
@requires_privileges
def test_bitmap_single_volume(nbd_env):
    vol = create_volume(nbd_env, "qcow2", "sparse")

    # Write first cluster - this cluster is not recorded in any bitmap.
    qemuio.write_pattern(
        vol.volumePath, "qcow2", offset=1 * MiB, len=64 * KiB, pattern=0xf1)

    # Add bitmap 1 and write second cluster.
    bitmap1 = str(uuid.uuid4())
    qemuimg.bitmap_add(vol.volumePath, bitmap1).run()
    qemuio.write_pattern(
        vol.volumePath, "qcow2", offset=2 * MiB, len=64 * KiB, pattern=0xf2)

    # Add bitmap 2 and write third cluster.
    bitmap2 = str(uuid.uuid4())
    qemuimg.bitmap_add(vol.volumePath, bitmap2).run()
    qemuio.write_pattern(
        vol.volumePath, "qcow2", offset=3 * MiB, len=64 * KiB, pattern=0xf3)

    # Test bitmap 1 - recording changes since bitmap 1 was added.

    config = {
        "sd_id": vol.sdUUID,
        "img_id": vol.imgUUID,
        "vol_id": vol.volUUID,
        "readonly": True,
        "bitmap": bitmap1,
    }

    with nbd_server(config) as nbd_url:
        with nbd_client.open(urlparse(nbd_url), dirty=True) as c:
            extents = c.extents(0, nbd_env.virtual_size)

            dirty_extents = [(e.length, e.dirty)
                             for e in extents[c.dirty_bitmap]]

            assert dirty_extents == [
                (2 * MiB, False),
                (64 * KiB, True),
                (1 * MiB - 64 * KiB, False),
                (64 * KiB, True),
                (nbd_env.virtual_size - 3 * MiB - 64 * KiB, False),
            ]

            assert c.read(1 * MiB, 64 * KiB) == b"\xf1" * 64 * KiB
            assert c.read(2 * MiB, 64 * KiB) == b"\xf2" * 64 * KiB

    # Test bitmap 2 - recording changes since bitmap 2 was added.

    config = {
        "sd_id": vol.sdUUID,
        "img_id": vol.imgUUID,
        "vol_id": vol.volUUID,
        "readonly": True,
        "bitmap": bitmap2,
    }

    with nbd_server(config) as nbd_url:
        with nbd_client.open(urlparse(nbd_url), dirty=True) as c:
            extents = c.extents(0, nbd_env.virtual_size)

            dirty_extents = [(e.length, e.dirty)
                             for e in extents[c.dirty_bitmap]]

            assert dirty_extents == [
                (3 * MiB, False),
                (64 * KiB, True),
                (nbd_env.virtual_size - 3 * MiB - 64 * KiB, False),
            ]

            assert c.read(2 * MiB, 64 * KiB) == b"\xf2" * 64 * KiB


@broken_on_ci
@requires_privileges
def test_bitmap_backing_chain(nbd_env):
    vol1 = create_volume(nbd_env, "raw", "sparse")

    # Write first cluster to vol1 - this cluster is not recorded in any bitmap.
    qemuio.write_pattern(
        vol1.volumePath, "raw", offset=1 * MiB, len=64 * KiB, pattern=0xf1)

    # Simulate a snapshot - bitmap1 is created empty in vol2.
    vol2 = create_volume(nbd_env, "qcow2", "sparse", parent=vol1)
    bitmap1 = str(uuid.uuid4())
    qemuimg.bitmap_add(vol2.volumePath, bitmap1).run()

    # Write second cluster in vol2 - this cluster is recorded only in bitmap 1.
    qemuio.write_pattern(
        vol2.volumePath, "qcow2", offset=2 * MiB, len=64 * KiB, pattern=0xf2)

    # Simulate another snapshot - bitmap1 is created empty in vol3.
    vol3 = create_volume(nbd_env, "qcow2", "sparse", parent=vol2)
    qemuimg.bitmap_add(vol3.volumePath, bitmap1).run()

    # Simulate backup - bitmap 2 is created in vol3.
    bitmap2 = str(uuid.uuid4())
    qemuimg.bitmap_add(vol3.volumePath, bitmap2).run()

    # Write third cluster in vol3. This cluster is recorded in both bitmaps.
    qemuio.write_pattern(
        vol3.volumePath, "qcow2", offset=3 * MiB, len=64 * KiB, pattern=0xf3)

    # Test bitmap 1 - recording changes since bitmap 1 was added.

    config = {
        "sd_id": vol3.sdUUID,
        "img_id": vol3.imgUUID,
        "vol_id": vol3.volUUID,
        "readonly": True,
        "bitmap": bitmap1,
    }

    with nbd_server(config) as nbd_url:
        with nbd_client.open(urlparse(nbd_url), dirty=True) as c:
            extents = c.extents(0, nbd_env.virtual_size)

            dirty_extents = [(e.length, e.dirty)
                             for e in extents[c.dirty_bitmap]]

            assert dirty_extents == [
                (2 * MiB, False),
                (64 * KiB, True),
                (1 * MiB - 64 * KiB, False),
                (64 * KiB, True),
                (nbd_env.virtual_size - 3 * MiB - 64 * KiB, False),
            ]

            assert c.read(1 * MiB, 64 * KiB) == b"\xf1" * 64 * KiB
            assert c.read(2 * MiB, 64 * KiB) == b"\xf2" * 64 * KiB

    # Test bitmap 2 - recording changes since bitmap 2 was added.

    config = {
        "sd_id": vol3.sdUUID,
        "img_id": vol3.imgUUID,
        "vol_id": vol3.volUUID,
        "readonly": True,
        "bitmap": bitmap2,
    }

    with nbd_server(config) as nbd_url:
        with nbd_client.open(urlparse(nbd_url), dirty=True) as c:
            extents = c.extents(0, nbd_env.virtual_size)

            dirty_extents = [(e.length, e.dirty)
                             for e in extents[c.dirty_bitmap]]

            assert dirty_extents == [
                (3 * MiB, False),
                (64 * KiB, True),
                (nbd_env.virtual_size - 3 * MiB - 64 * KiB, False),
            ]

            assert c.read(2 * MiB, 64 * KiB) == b"\xf2" * 64 * KiB


@broken_on_ci
@requires_privileges
def test_bitmap_in_use_single_volume(nbd_env):
    vol = create_volume(nbd_env, "qcow2", "sparse")

    bitmap = str(uuid.uuid4())
    qemuimg.bitmap_add(vol.volumePath, bitmap).run()

    # Simulate qemu crash, leaving bitmaps with the "in-use" flag by opening
    # the image for writing and killing the process.
    qemuio.abort(vol.volumePath)

    config = {
        "sd_id": vol.sdUUID,
        "img_id": vol.imgUUID,
        "vol_id": vol.volUUID,
        "readonly": True,
        "bitmap": bitmap,
    }

    # Starting server will fail since the bitmap in vol is in use.
    with pytest.raises(se.InvalidBitmapChain):
        with nbd_server(config):
            pass


@broken_on_ci
@requires_privileges
def test_bitmap_in_use_backing_chain(nbd_env):
    vol1 = create_volume(nbd_env, "qcow2", "sparse")
    vol2 = create_volume(nbd_env, "qcow2", "sparse", parent=vol1)

    bitmap = str(uuid.uuid4())
    qemuimg.bitmap_add(vol1.volumePath, bitmap).run()
    qemuimg.bitmap_add(vol2.volumePath, bitmap).run()

    # Simulate qemu crash, leaving bitmaps with the "in-use" flag by opening
    # the image for writing and killing the process.
    qemuio.abort(vol2.volumePath)

    config = {
        "sd_id": vol2.sdUUID,
        "img_id": vol2.imgUUID,
        "vol_id": vol2.volUUID,
        "readonly": True,
        "bitmap": bitmap,
    }

    # Starting server will fail since the bitmap in vol2 is in in use.
    with pytest.raises(se.InvalidBitmapChain):
        with nbd_server(config):
            pass


@broken_on_ci
@requires_privileges
def test_bitmap_disabled_single_volume(nbd_env):
    vol = create_volume(nbd_env, "qcow2", "sparse")

    bitmap = str(uuid.uuid4())

    # Add disabled bitmap.
    qemuimg.bitmap_add(vol.volumePath, bitmap, enable=False).run()

    config = {
        "sd_id": vol.sdUUID,
        "img_id": vol.imgUUID,
        "vol_id": vol.volUUID,
        "readonly": True,
        "bitmap": bitmap,
    }

    # Starting server will fail since the bitmap is disabled.
    with pytest.raises(se.InvalidBitmapChain):
        with nbd_server(config):
            pass


@broken_on_ci
@requires_privileges
def test_bitmap_disabled_backing_chain(nbd_env):
    vol1 = create_volume(nbd_env, "qcow2", "sparse")
    vol2 = create_volume(nbd_env, "qcow2", "sparse", parent=vol1)

    bitmap = str(uuid.uuid4())

    # Add bitmap to both volumes, but disable it on vol1.
    qemuimg.bitmap_add(vol1.volumePath, bitmap, enable=False).run()
    qemuimg.bitmap_add(vol2.volumePath, bitmap).run()

    config = {
        "sd_id": vol2.sdUUID,
        "img_id": vol2.imgUUID,
        "vol_id": vol2.volUUID,
        "readonly": True,
        "bitmap": bitmap,
    }

    # Starting server will fail since the bitmap in vol1 is disabled.
    with pytest.raises(se.InvalidBitmapChain):
        with nbd_server(config):
            pass


@broken_on_ci
@requires_privileges
def test_bitmap_missing_single_volume(nbd_env):
    vol = create_volume(nbd_env, "qcow2", "sparse")

    bitmap = str(uuid.uuid4())

    config = {
        "sd_id": vol.sdUUID,
        "img_id": vol.imgUUID,
        "vol_id": vol.volUUID,
        "readonly": True,
        "bitmap": bitmap,
    }

    # Starting server will fail since the bitmap is missing.
    with pytest.raises(se.BitmapDoesNotExist) as e:
        with nbd_server(config):
            pass

    # Check that we report the volume where the bitmap is missing.
    assert vol.volumePath in str(e.value)


@broken_on_ci
@requires_privileges
def test_bitmap_missing_top(nbd_env):
    vol1 = create_volume(nbd_env, "qcow2", "sparse")
    vol2 = create_volume(nbd_env, "qcow2", "sparse", parent=vol1)
    vol3 = create_volume(nbd_env, "qcow2", "sparse", parent=vol2)

    bitmap = str(uuid.uuid4())

    # Add the bitmap to vol1, but not to vol2 and vol3.
    qemuimg.bitmap_add(vol1.volumePath, bitmap).run()

    config = {
        "sd_id": vol3.sdUUID,
        "img_id": vol3.imgUUID,
        "vol_id": vol3.volUUID,
        "readonly": True,
        "bitmap": bitmap,
    }

    # Starting server will fail since the bitmap is missing in vol2.
    with pytest.raises(se.InvalidBitmapChain) as e:
        with nbd_server(config):
            pass

    # Check that we report the volumes where the bitmap is missing.
    assert vol2.volumePath in str(e.value)
    assert vol3.volumePath in str(e.value)


@broken_on_ci
@requires_privileges
def test_bitmap_missing_middle(nbd_env):
    vol1 = create_volume(nbd_env, "qcow2", "sparse")
    vol2 = create_volume(nbd_env, "qcow2", "sparse", parent=vol1)
    vol3 = create_volume(nbd_env, "qcow2", "sparse", parent=vol2)

    bitmap = str(uuid.uuid4())

    # Add the bitmap to vol1 and vol3, but not to vol2.
    qemuimg.bitmap_add(vol1.volumePath, bitmap).run()
    qemuimg.bitmap_add(vol3.volumePath, bitmap).run()

    config = {
        "sd_id": vol3.sdUUID,
        "img_id": vol3.imgUUID,
        "vol_id": vol3.volUUID,
        "readonly": True,
        "bitmap": bitmap,
    }

    # Starting server will fail since the bitmap is missing in vol2.
    with pytest.raises(se.InvalidBitmapChain) as e:
        with nbd_server(config):
            pass

    # Check that we report the volumes where the bitmap is missing.
    assert vol2.volumePath in str(e.value)


@broken_on_ci
@requires_privileges
def test_bitmap_does_not_exist_backing_chain(nbd_env):
    vol1 = create_volume(nbd_env, "raw", "sparse")
    vol2 = create_volume(nbd_env, "qcow2", "sparse", parent=vol1)

    bitmap = str(uuid.uuid4())

    # Add another bitmap to the volume.
    qemuimg.bitmap_add(vol2.volumePath, str(uuid.uuid4())).run()

    config = {
        "sd_id": vol2.sdUUID,
        "img_id": vol2.imgUUID,
        "vol_id": vol2.volUUID,
        "readonly": True,
        "bitmap": bitmap,
    }

    # Starting server will fail since bitmap does not exist.
    with pytest.raises(se.BitmapDoesNotExist):
        with nbd_server(config):
            pass


@broken_on_ci
@requires_privileges
def test_server_socket_mode(nbd_env):
    vol = create_volume(nbd_env, "qcow2", "sparse")

    config = {
        "sd_id": vol.sdUUID,
        "img_id": vol.imgUUID,
        "vol_id": vol.volUUID,
    }

    with nbd_server(config) as nbd_url:
        # Remove "nbd:unix:" from nbd_url.
        socket = nbd_url[9:]

        actual_mode = stat.S_IMODE(os.stat(socket).st_mode)
        assert oct(actual_mode) == oct(0o660)


def test_shared_volume(nbd_env):
    vol = create_volume(nbd_env, "qcow2", "sparse")
    vol.setShared()

    config = {
        "sd_id": vol.sdUUID,
        "img_id": vol.imgUUID,
        "vol_id": vol.volUUID,
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


def upload_to_nbd(filename, nbd_url):
    op = qemuimg.convert(
        filename,
        nbd_url,
        srcFormat="qcow2",
        create=False,
        target_is_zero=True)
    op.run()


def download_from_nbd(nbd_url, filename):
    op = qemuimg.convert(
        nbd_url, filename, dstFormat="qcow2", dstQcow2Compat="1.1")
    op.run()


def compare_images(a, b, strict=False):
    op = qemuimg.compare(a, b, strict=strict)
    op.run()


def create_volume(env, format, allocation, parent=None):
    vol_id = str(uuid.uuid4())

    if parent:
        img_id = parent.imgUUID
        parent_vol_id = parent.volUUID
    else:
        img_id = str(uuid.uuid4())
        parent_vol_id = sc.BLANK_UUID

    env.make_volume(
        env.virtual_size,
        img_id,
        vol_id,
        parent_vol_id=parent_vol_id,
        vol_format=sc.str2fmt(format),
        prealloc=sc.name2type(allocation),
        qcow2_compat="1.1")

    return env.sd_manifest.produceVolume(img_id, vol_id)
