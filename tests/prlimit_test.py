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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA
# 02110-1301  USA
#
# Refer to the README and COPYING files for full details of the license
#

from __future__ import absolute_import
from __future__ import division

import os
import pytest

from vdsm.common import cmdutils
from vdsm.common import commands
from vdsm.common.units import MiB


@pytest.mark.skipif(
    "OVIRT_CI" in os.environ,
    reason="prlimit --cpu does not work in oVirt CI envrinoment")
def test_limit_cpu():
    # This takes 6 seconds on i7-5600U CPU @ 2.60GHz. We assume that it will
    # never take less then 1 second. Increase n if this starts to fail
    # randomly.
    script = """
n = 2**27
while n:
    n -= 1
"""
    cmd = ["python", "-c", script]
    cmd = cmdutils.prlimit(cmd, cpu_time=1)
    with pytest.raises(cmdutils.Error) as e:
        commands.run(cmd)
    assert e.value.rc == -9


def test_limit_rss():
    # This should fail to allocate about 100 MiB.
    script = "s = 100 * 1024**2 * 'x'"
    cmd = ["python", "-c", script]
    cmd = cmdutils.prlimit(cmd, address_space=100 * MiB)
    with pytest.raises(cmdutils.Error) as e:
        commands.run(cmd)
    assert e.value.rc == 1
    assert b"MemoryError" in e.value.err


def test_true():
    # true should succeed with these limits.
    cmd = cmdutils.prlimit(["true"], address_space=100 * MiB, cpu_time=1)
    assert commands.run(cmd) == b''
