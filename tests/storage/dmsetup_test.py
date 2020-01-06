#
# Copyright 2020 Red Hat, Inc.
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

from __future__ import absolute_import
from __future__ import division

import pytest

from vdsm.storage import dmsetup
from .marks import requires_root

DMSETUP_SCRIPT = """\
#!/bin/sh
set -e

# Run the real dmsetup to validate the arguments, dropping the output.
dmsetup "$@" > /dev/null

# Fake the output
echo -n '{}'
"""

FAKE_DMSETUP_OUTPUT = """\
360014053d0b83eff3d347c48509fc426: 0 104857600 multipath 2 0 1 0 3 2 E 0 1 1 67:16 F 4 0 E 0 1 1 65:240 A 84 0 E 0 1 1 66:64 A 39 0
3600140543cb8d7510d54f058c7b3f7ec: 0 209715200 multipath 2 0 1 0 3 1 A 0 1 1 65:224 A 0 0 E 0 1 1 65:160 A 0 0 E 0 1 1 66:176 F 1 0
"""  # NOQA: E501 (long line)

# Output if there are no dm devices on the host.
NO_DEVICE_MAPPER_DEVICES = b"No devices found\n"

# Output if no multipath device is found and there are dm devices on the host.
NO_MULTIPATH_DEVICE = b""


class FakeDmSetupStatus(object):

    def __init__(self):
        self.out = {}

    def __call__(self, target=None):
        return self.out


@pytest.fixture
def fake_run_status(monkeypatch):
    monkeypatch.setattr(dmsetup, "run_status", FakeDmSetupStatus())


@pytest.fixture
def fake_dmsetup(monkeypatch, fake_executable):
    monkeypatch.setattr(dmsetup, "EXT_DMSETUP", str(fake_executable))
    return fake_executable


@requires_root
def test_status(fake_dmsetup):
    fake_dmsetup.write(DMSETUP_SCRIPT.format(FAKE_DMSETUP_OUTPUT))

    res = dmsetup.status(target="multipath")
    expected = [
        ("360014053d0b83eff3d347c48509fc426", " 0 104857600 multipath 2 0 1 0 3 2 E 0 1 1 67:16 F 4 0 E 0 1 1 65:240 A 84 0 E 0 1 1 66:64 A 39 0"),  # NOQA: E501 (long line)
        ("3600140543cb8d7510d54f058c7b3f7ec", " 0 209715200 multipath 2 0 1 0 3 1 A 0 1 1 65:224 A 0 0 E 0 1 1 65:160 A 0 0 E 0 1 1 66:176 F 1 0"),  # NOQA: E501 (long line)
    ]

    assert list(res) == expected


def test_status_no_device(fake_run_status):
    dmsetup.run_status.out = NO_DEVICE_MAPPER_DEVICES
    assert list(dmsetup.status()) == []


def test_status_no_output(fake_run_status):
    dmsetup.run_status.out = NO_MULTIPATH_DEVICE
    assert list(dmsetup.status()) == []
