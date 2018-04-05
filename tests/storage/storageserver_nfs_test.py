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

import pytest

from vdsm.storage.storageServer import NFSConnection


def test_extra_options():
    c = NFSConnection("id", "address", version="4.2",
                      extraOptions="extra,options")
    options = c._mountCon.options.split(",")
    assert "extra" in options
    assert "options" in options


def test_extra_options_filter_empty():
    c = NFSConnection("id", "address", version="4.2",
                      extraOptions=",extra,,options,")
    options = c._mountCon.options.split(",")
    assert "" not in options
    assert "extra" in options
    assert "options" in options


@pytest.mark.parametrize("version", [None, "3"])
def test_nfs3_locks_disabled(version):
    c = NFSConnection("id", "address", version=version)
    options = c._mountCon.options.split(",")
    assert "nolock" in options


@pytest.mark.parametrize("version", [None, "3"])
def test_nfs3_locks_override(version):
    c = NFSConnection("id", "address", version=version, extraOptions="lock")
    options = c._mountCon.options.split(",")
    assert "lock" in options
    assert "nolock" not in options


@pytest.mark.parametrize("version", [None, "3"])
def test_nfs3_locks_pass(version):
    c = NFSConnection("id", "address", version=version, extraOptions="nolock")
    options = c._mountCon.options.split(",")
    assert options.count("nolock") == 1


@pytest.mark.parametrize("version", ["4", "4.0", "4.1", "4.2"])
def test_nfs3_locks_version_4(version):
    c = NFSConnection("id", "address", version=version)
    options = c._mountCon.options.split(",")
    assert "nolock" not in options
