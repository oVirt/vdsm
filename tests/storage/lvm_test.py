#
# Copyright 2012-2019 Red Hat, Inc.
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

from vdsm.storage import lvm


def test_build_filter():
    devices = ("/dev/mapper/a", "/dev/mapper/b")
    expected = "filter=['a|/dev/mapper/a|/dev/mapper/b|', 'r|.*|']"
    assert expected == lvm._buildFilter(devices)


def test_build_filter_quoting():
    devices = (r"\x20\x24\x7c\x22\x28",)
    expected = r"filter=['a|\\x20\\x24\\x7c\\x22\\x28|', 'r|.*|']"
    assert expected == lvm._buildFilter(devices)


def test_build_config():
    devices = ("/dev/a", "/dev/b")
    expected = (
        'devices { '
        ' preferred_names=["^/dev/mapper/"] '
        ' ignore_suspended_devices=1 '
        ' write_cache_state=0 '
        ' disable_after_error_count=3 '
        " filter=['a|/dev/a|/dev/b|', 'r|.*|'] "
        '} '
        'global { '
        ' locking_type=1 '
        ' prioritise_write_locks=1 '
        ' wait_for_locks=1 '
        ' use_lvmetad=0 '
        '} '
        'backup { '
        ' retain_min=50 '
        ' retain_days=0 '
        '}'
    )
    assert expected == lvm._buildConfig(devices)
