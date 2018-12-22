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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#

from __future__ import absolute_import
from __future__ import division

import os
import uuid

import pytest

from vdsm.common import systemctl
from vdsm.common import systemd

requires_root = pytest.mark.skipif(
    os.geteuid() != 0, reason="requires root")

broken_on_ci = pytest.mark.skipif(
    "OVIRT_CI" in os.environ or "TRAVIS_CI" in os.environ,
    reason="requires systemd daemon")


@broken_on_ci
def test_show_unit_not_found():
    unit = "test-sleep-{}.service".format(uuid.uuid4())
    properties = ("Names", "LoadState", "ActiveState")

    r = systemctl.show(unit, properties=properties)
    assert r == [{
        "ActiveState": "inactive",
        "LoadState": "not-found",
        "Names": unit,
    }]

    r = systemctl.show(unit)
    assert len(r) == 1
    assert r[0]["ActiveState"] == "inactive"
    assert r[0]["LoadState"] == "not-found"
    assert r[0]["Names"] == unit


@broken_on_ci
def test_show_pattern_not_found():
    pattern = "test-*-{}.service".format(uuid.uuid4())
    properties = ("Names", "LoadState", "ActiveState")

    r = systemctl.show(pattern, properties=properties)
    assert r == []

    r = systemctl.show(pattern)
    assert r == []


@requires_root
@broken_on_ci
def test_single_unit():
    unit = "test-sleep-{}.service".format(uuid.uuid4())
    properties = ("Names", "LoadState", "ActiveState")

    systemd.run(["sleep", "5"], unit=unit)
    try:

        r = systemctl.show(unit, properties=properties)
        assert r == [{
            "ActiveState": "active",
            "LoadState": "loaded",
            "Names": unit,
        }]

        r = systemctl.show(unit)
        assert len(r) == 1
        assert r[0]["ActiveState"] == "active"
        assert r[0]["LoadState"] == "loaded"
        assert r[0]["Names"] == unit

    finally:
        systemctl.stop(unit)

    r = systemctl.show(unit, properties=properties)
    assert r == [{
        "ActiveState": "inactive",
        "LoadState": "not-found",
        "Names": unit,
    }]


@requires_root
@broken_on_ci
def test_multiple_units():
    unit1 = "test-sleep-{}.service".format(uuid.uuid4())
    unit2 = "test-sleep-{}.service".format(uuid.uuid4())
    pattern = "test-sleep-*.service"
    properties = ("Names", "LoadState", "ActiveState")

    systemd.run(["sleep", "5"], unit=unit1)
    try:

        r = systemctl.show(pattern, properties=properties)
        assert r == [
            {
                "ActiveState": "active",
                "LoadState": "loaded",
                "Names": unit1,
            },
        ]

        systemd.run(["sleep", "5"], unit=unit2)

        r = systemctl.show(pattern, properties=properties)
        assert r == [
            {
                "ActiveState": "active",
                "LoadState": "loaded",
                "Names": unit1,
            },
            {
                "ActiveState": "active",
                "LoadState": "loaded",
                "Names": unit2,
            },
        ]

        r = systemctl.show(pattern)
        assert len(r) == 2
        assert r[0]["LoadState"] == "loaded"
        assert r[0]["Names"] == unit1
        assert r[1]["LoadState"] == "loaded"
        assert r[1]["Names"] == unit2

    finally:
        systemctl.stop(pattern)

    r = systemctl.show(pattern, properties=properties)
    assert r == []
