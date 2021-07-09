# Copyright 2020 Red Hat, Inc.
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

import pytest

from vdsm.network import errors as ne
from vdsm.network.link.bridge import Bridge

from network.nettestlib import Bridge as TestBridge

BR1_NAME = 'br1'


@pytest.fixture
def bridge():
    bridge = TestBridge()
    bridge.create()
    bridge.down()
    yield bridge.dev_name
    bridge.remove()


def test_write_custom_bridge_options(bridge):
    options1 = {'multicast_router': '0', 'multicast_snooping': '0'}
    options2 = {'multicast_router': '1', 'multicast_snooping': '1'}

    br = Bridge(bridge, options1)

    for opt, val in options1.items():
        assert br.options.get(opt) == val

    br.set_options(options2)

    for opt, val in options2.items():
        assert br.options.get(opt) == val


def test_write_no_custom_bridge_options(bridge):
    br = Bridge(bridge)
    initial_opts = br.options

    br.set_options({})

    assert br.options == initial_opts


def test_get_non_existent_bridge_opt_with_sysfs_fails():
    options = {'fake': 'opt'}
    with pytest.raises(ne.ConfigNetworkError) as e:
        Bridge(BR1_NAME, options)
    assert e.value.errCode == ne.ERR_BAD_PARAMS
