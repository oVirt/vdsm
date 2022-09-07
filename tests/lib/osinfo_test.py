# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

import pytest
import tempfile

from vdsm import osinfo


@pytest.fixture
def fake_findmnt(monkeypatch, fake_executeable):
    monkeypatch.setattr(osinfo, "_FINDMNT", str(fake_executeable))
    return fake_executeable


kernel_args = [
    (b'', ''),
    (b'\n', ''),
    (b'a', 'a'),
    (b'a\n', 'a'),
    (b'a\nb', 'a')
]


@pytest.mark.parametrize("test_input, expected_result", kernel_args)
def test_kernel_args(test_input, expected_result):
    with tempfile.NamedTemporaryFile() as f:
        f.write(test_input)
        f.flush()
        assert osinfo.kernel_args(f.name) == expected_result


def test_package_versions():
    pkgs = osinfo.package_versions()
    assert 'kernel' in pkgs


def test_get_boot_uuid(fake_findmnt):
    fake_script = """\
    #!/bin/sh
    # Normally, we would run the real findmnt to validate the arguments
    #
    # However, findmnt will return a random UUID for every boot partition it
    # runs on. Therefore we skip the findmnt test run and just fake the
    # output.
    echo '{}'
    """
    fake_findmnt.write(fake_script
                       .format("f3d3c716-54a0-4cd8-8ee1-d49f88e9cb11\n"))
    assert osinfo.boot_uuid() == "f3d3c716-54a0-4cd8-8ee1-d49f88e9cb11"


def test_get_boot_uuid_error(monkeypatch):
    monkeypatch.setattr(osinfo, "_FINDMNT", "false")
    osinfo.boot_uuid.invalidate()
    with pytest.raises(Exception):
        osinfo.boot_uuid()
