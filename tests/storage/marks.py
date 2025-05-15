# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

import os

import selinux
import pytest

from vdsm.common import cache
from vdsm.common import commands

from testing import (
    on_ovirt_ci,
    on_travis_ci,
)


requires_root = pytest.mark.skipif(
    os.geteuid() != 0, reason="requires root")

requires_unprivileged_user = pytest.mark.skipif(
    os.geteuid() == 0, reason="This test can not run as root")

requires_selinux = pytest.mark.skipif(
    not selinux.is_selinux_enabled(), reason="Selinux is not enabled")

broken_on_ci = pytest.mark.skipif(
    on_ovirt_ci() or on_travis_ci(), reason="fails on CI")


@cache.memoized
def has_loopback_sector_size():
    out = commands.run(["losetup", "-h"])
    return "--sector-size <num>" in out.decode()


requires_loopback_sector_size = pytest.mark.skipif(
    not has_loopback_sector_size(),
    reason="lossetup --sector-size option not available")
