# Copyright 2013-2018 Red Hat, Inc.
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
import six

from pwd import getpwnam

import pytest

from vdsm.common import supervdsm
from vdsm.constants import VDSM_USER


@pytest.fixture
def dropped_privileges():
    vdsm_uid, vdsm_gid = getpwnam(VDSM_USER)[2:4:]
    os.setgroups([])
    os.setgid(vdsm_gid)
    os.setuid(vdsm_uid)


@pytest.mark.skipif(os.geteuid() != 0, reason="Requires root")
def test_ping_call(dropped_privileges):
    proxy = supervdsm.getProxy()
    assert bool(proxy.ping())


# This requires environment with tmpfs mounted to /sys/kernel/mm/ksm
@pytest.mark.skipif(os.geteuid() != 0, reason="Requires root")
def test_ksm_action(dropped_privileges):
    proxy = supervdsm.getProxy()
    ksmParams = {"run": 0,
                 "merge_across_nodes": 1,
                 "sleep_millisecs": 0xffff,
                 "pages_to_scan": 0xffff}
    proxy.ksmTune(ksmParams)

    for k, v in six.iteritems(ksmParams):
        with open("/sys/kernel/mm/ksm/%s" % k, "r") as f:
            assert str(v) == f.read().rstrip()
