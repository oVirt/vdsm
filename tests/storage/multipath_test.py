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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#

from __future__ import absolute_import
from __future__ import division

import pytest

from vdsm.common import cmdutils
from vdsm.storage import multipath

from . marks import requires_root, xfail_python3

MULTIPATHD_SCRIPT = """\
#!/bin/sh

# Normally, we would run the real multipathd to validate the arguments and
# dropping the output with
#
# multipathd "$@" > /dev/null
#
# However, multipathd requires /etc/multipath.conf to be present and also DM
# multipath kernel driver to be loaded, otherwise fails. As this is not by
# default configured and enabled, skip the multipath test run and just fake the
# output.

echo '{}'
"""


@pytest.fixture
def fake_multipathd(monkeypatch, fake_executeable):
    monkeypatch.setattr(
        multipath,
        "_MULTIPATHD",
        cmdutils.CommandPath("fake-multipathd", str(fake_executeable))
    )

    return fake_executeable


@xfail_python3
@requires_root
def test_resize_map(fake_multipathd):
    fake_multipathd.write(MULTIPATHD_SCRIPT.format("ok"))
    multipath.resize_map("fake_device")


@requires_root
def test_resize_map_failed(fake_multipathd):
    fake_multipathd.write(MULTIPATHD_SCRIPT.format("fail"))

    with pytest.raises(multipath.Error):
        multipath.resize_map("fake_device")
