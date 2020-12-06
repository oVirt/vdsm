#
# Copyright 2019 Red Hat, Inc.
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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA
# 02110-1301  USA
#
# Refer to the README and COPYING files for full details of the license
#

from __future__ import absolute_import
from __future__ import division

import os
import sys

import selinux
import six
import pytest

from vdsm.common import cache
from vdsm.common import commands
from vdsm.common import compat

from vdsm.storage import qemuimg
from vdsm.storage.compat import sanlock

from testing import (
    on_fedora,
    on_ovirt_ci,
    on_travis_ci,
)


requires_root = pytest.mark.skipif(
    os.geteuid() != 0, reason="requires root")

requires_unprivileged_user = pytest.mark.skipif(
    os.geteuid() == 0, reason="This test can not run as root")

requires_sanlock = pytest.mark.skipif(
    isinstance(sanlock, compat.MissingModule),
    reason="sanlock is not available")

requires_selinux = pytest.mark.skipif(
    not selinux.is_selinux_enabled(), reason="Selinux is not enabled")

xfail_python3 = pytest.mark.xfail(
    six.PY3, reason="needs porting to python 3")

xfail_python37 = pytest.mark.xfail(
    sys.version_info[:2] == (3, 7), reason="needs porting to python 3.7")

# Note: This cannot be strict since in oVirt CI we run with older qemu-img
# version that does not reproduce this issue.
xfail_requires_target_is_zero = pytest.mark.xfail(
    not qemuimg.target_is_zero_supported(),
    reason="requires qemu-img convert --target-is-zero introdued in 5.1.0",
    strict=False)

broken_on_ci = pytest.mark.skipif(
    on_ovirt_ci() or on_travis_ci(), reason="fails on CI")


@cache.memoized
def has_loopback_sector_size():
    out = commands.run(["losetup", "-h"])
    return "--sector-size <num>" in out.decode()


requires_loopback_sector_size = pytest.mark.skipif(
    not has_loopback_sector_size(),
    reason="lossetup --sector-size option not available")


requires_bitmaps_support = pytest.mark.skipif(
    not qemuimg.bitmaps_supported(),
    reason="qemu-img does not support bitmaps operations")


requires_bitmaps_merge_support = pytest.mark.skipif(
    not qemuimg.bitmaps_supported() or on_fedora(),
    reason="qemu-img does not have a working bitmap --merge")
