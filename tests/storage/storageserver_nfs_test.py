# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

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
