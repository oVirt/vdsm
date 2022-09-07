# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

import io
import os

import pytest

from vdsm.common import cmdutils
from vdsm.storage import fallocate


def test_allocate(tmpdir, monkeypatch):
    # Test that allocate call works correctly.
    monkeypatch.setattr(fallocate, '_FALLOCATE', '../helpers/fallocate')
    size = 4096
    image = str(tmpdir.join("image"))

    fallocate.allocate(image, size).run()

    allocated = os.stat(image).st_blocks * 512
    assert allocated == size


def test_negative_size(tmpdir, monkeypatch):
    # Test that fallocate call throws exception on error
    monkeypatch.setattr(fallocate, '_FALLOCATE', '../helpers/fallocate')
    image = str(tmpdir.join("image"))
    with pytest.raises(cmdutils.Error):
        fallocate.allocate(image, -1).run()


def test_zero_size(tmpdir, monkeypatch):
    # Test that fallocate call throws exception on error
    monkeypatch.setattr(fallocate, '_FALLOCATE', '../helpers/fallocate')
    image = str(tmpdir.join("image"))
    with pytest.raises(cmdutils.Error):
        fallocate.allocate(image, 0).run()


def test_resize(tmpdir, monkeypatch):
    # Test that resize call actually works
    monkeypatch.setattr(fallocate, '_FALLOCATE', '../helpers/fallocate')
    size = 4096
    image = str(tmpdir.join("image"))

    with io.open(image, "wb") as f:
        f.write(b'x' * size)

    fallocate.allocate(image, size, offset=size).run()

    with io.open(image, 'rb') as f:
        actual = f.read()

    expected = b'x' * size + b'\0' * size

    assert expected == actual

    allocated = os.stat(image).st_blocks * 512
    assert allocated == size * 2
