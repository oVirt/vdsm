#
# Copyright 2009-2019 Red Hat, Inc.
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

import os

import pytest

from vdsm.storage import managedvolume

requires_root = pytest.mark.skipif(
    os.geteuid() != 0, reason="requires root")


@pytest.fixture
def fake_os_brick(monkeypatch):
    monkeypatch.setattr(
        managedvolume, 'HELPER', "../lib/vdsm/storage/managedvolume-helper")
    os_brick_dir = os.path.abspath("storage/fake_os_brick")
    monkeypatch.setenv("PYTHONPATH", os_brick_dir, prepend=":")

    # os_brick may not be available yet on developers machines. Make sure we
    # test with our fake os_brick.
    monkeypatch.setattr(managedvolume, "os_brick", object())


@requires_root
def test_os_brick_not_installed(monkeypatch):
    # Simulate missing os_brick.
    monkeypatch.setattr(managedvolume, "os_brick", None)
    with pytest.raises(managedvolume.NotSupported):
        managedvolume.connector_info()


@requires_root
def test_fake_os_brick_ok(monkeypatch, fake_os_brick):
    monkeypatch.setenv("FAKE_OS_BRICK_RESULT", "OK")
    assert managedvolume.connector_info() == {"fake": "True"}


@requires_root
def test_fake_os_brick_fail(monkeypatch, fake_os_brick):
    monkeypatch.setenv("FAKE_OS_BRICK_RESULT", "FAIL")
    with pytest.raises(managedvolume.HelperFailed):
        managedvolume.connector_info()


@requires_root
def test_fake_os_brick_fail_json(monkeypatch, fake_os_brick):
    monkeypatch.setenv("FAKE_OS_BRICK_RESULT", "FAIL_JSON")
    with pytest.raises(managedvolume.HelperFailed):
        managedvolume.connector_info()


@requires_root
def test_fake_os_brick_fail_raise(monkeypatch, fake_os_brick):
    monkeypatch.setenv("FAKE_OS_BRICK_RESULT", "RAISE")
    with pytest.raises(managedvolume.HelperFailed) as e:
        managedvolume.connector_info()
    assert "error message from os_brick" in str(e.value)
