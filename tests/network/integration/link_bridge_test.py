# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

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
