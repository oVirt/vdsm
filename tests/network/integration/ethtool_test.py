# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

import pytest

from vdsm.network import ethtool

from network.nettestlib import bridge_device


@pytest.fixture
def bridge():
    with bridge_device() as br:
        yield br


class TestEthtoolDeviceInfo(object):
    def test_detect_device_driver(self, bridge):
        assert ethtool.driver_name(bridge) == 'bridge'
