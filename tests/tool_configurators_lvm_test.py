# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

import os

from vdsm.tool import confmeta

TOPDIR = os.path.dirname(os.path.dirname(__file__))


def test_lvmlocal_conf():
    lvmlocal_conf = os.path.join(
        TOPDIR, "static/usr/share/vdsm/lvmlocal.conf")
    md = confmeta.read_metadata(lvmlocal_conf)
    assert md.revision >= 7
    assert md.private is False
