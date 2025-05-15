# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

import os

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

    for k, v in ksmParams.items():
        with open("/sys/kernel/mm/ksm/%s" % k, "r") as f:
            assert str(v) == f.read().rstrip()
