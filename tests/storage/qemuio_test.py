# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

import pytest

from vdsm.common import cmdutils
from vdsm.storage import qemuimg

from . import qemuio


@pytest.fixture(params=[qemuimg.FORMAT.QCOW2, qemuimg.FORMAT.RAW])
def image_format(request):
    return request.param


def test_match(tmpdir, image_format):
    path = str(tmpdir.join('test.' + image_format))
    op = qemuimg.create(path, '1m', image_format)
    op.run()
    qemuio.write_pattern(path, image_format)
    qemuio.verify_pattern(path, image_format)


@pytest.mark.parametrize("offset,len", [(0, 128), (10 * 1024, 5 * 1024)])
def test_match_custom_offset_and_len(tmpdir, offset, len):
    path = str(tmpdir.join('test.qcow2'))
    op = qemuimg.create(path, '1m', qemuimg.FORMAT.QCOW2)
    op.run()
    qemuio.write_pattern(path, qemuimg.FORMAT.QCOW2, offset=offset, len=len)
    qemuio.verify_pattern(path, qemuimg.FORMAT.QCOW2, offset=offset, len=len)


def test_no_match(tmpdir, image_format):
    path = str(tmpdir.join('test.' + image_format))
    op = qemuimg.create(path, '1m', image_format)
    op.run()
    qemuio.write_pattern(path, image_format, pattern=2)
    with pytest.raises(qemuio.VerificationError):
        qemuio.verify_pattern(path, image_format, pattern=4)


def test_read_missing_file_raises(image_format):
    with pytest.raises(cmdutils.Error):
        qemuio.verify_pattern("/no/such/file", image_format)


def test_read_wrong_format_raises(tmpdir):
    path = str(tmpdir.join('test.raw'))
    qemuimg.create(path, "1m", qemuimg.FORMAT.RAW)
    with pytest.raises(cmdutils.Error):
        qemuio.verify_pattern(path, qemuimg.FORMAT.QCOW2)


def test_read_bad_chain_raises(tmpdir):
    # Create a good chain.
    base_qcow2 = str(tmpdir.join("base.qcow2"))
    op = qemuimg.create(base_qcow2, "1m", qemuimg.FORMAT.QCOW2)
    op.run()
    top = str(tmpdir.join("top.qcow2"))
    op = qemuimg.create(top, "1m", qemuimg.FORMAT.QCOW2,
                        backing=base_qcow2,
                        backingFormat=qemuimg.FORMAT.QCOW2)
    op.run()

    # Create a broken chain using unsafe rebase with the wrong backing
    # format.
    base_raw = str(tmpdir.join("base.raw"))
    op = qemuimg.create(base_raw, "1m", qemuimg.FORMAT.RAW)
    op.run()
    operation = qemuimg.rebase(top,
                               backing=base_raw,
                               format=qemuimg.FORMAT.QCOW2,
                               backingFormat=qemuimg.FORMAT.QCOW2,
                               unsafe=True)
    operation.run()
    with pytest.raises(cmdutils.Error):
        qemuio.verify_pattern(top, qemuimg.FORMAT.QCOW2)


@pytest.mark.parametrize("fmt", [
    pytest.param(
        qemuimg.FORMAT.RAW,
        marks=pytest.mark.xfail(reason="Always times out", run=False)),
    qemuimg.FORMAT.QCOW2
])
def test_open_write_mode(tmpdir, fmt):
    image = str(tmpdir.join("disk." + fmt))
    op = qemuimg.create(image, "1m", fmt)
    op.run()
    with qemuio.open(image, fmt):
        with pytest.raises(cmdutils.Error):
            qemuimg.info(image, fmt)
