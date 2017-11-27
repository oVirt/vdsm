#
# Copyright 2017 Red Hat, Inc.
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

import os
import pytest

from vdsm.storage import devicemapper
from vdsm.storage.devicemapper import PathStatus

FAKE_DMSETUP = os.path.join(os.path.dirname(__file__), "fake-dmsetup")


@pytest.mark.skipif(os.geteuid() != 0, reason="Requires root")
def test_dm_status(monkeypatch):
    monkeypatch.setattr(devicemapper, "EXT_DMSETUP", FAKE_DMSETUP)
    monkeypatch.setenv("FAKE_STDOUT", FAKE_DMSETUP + ".status.out")
    monkeypatch.setattr(
        devicemapper, "device_name", lambda major_minor: major_minor)

    res = devicemapper._multipath_status()
    expected = {
        '360014053d0b83eff3d347c48509fc426':
            [
                PathStatus('67:16', 'F'),
                PathStatus('65:240', 'A'),
                PathStatus('66:64', 'A')
            ],
        '3600140543cb8d7510d54f058c7b3f7ec':
            [
                PathStatus('65:224', 'A'),
                PathStatus('65:160', 'A'),
                PathStatus('66:176', 'F')
            ]
    }

    assert res == expected
