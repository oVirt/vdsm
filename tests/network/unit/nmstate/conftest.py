# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from unittest import mock

import pytest


from vdsm.network import nmstate
from vdsm.network.nmstate import api


@pytest.fixture(autouse=True)
def current_state_mock():
    with mock.patch.object(api, 'state_show') as state:
        state.return_value = {
            nmstate.Interface.KEY: [],
            nmstate.DNS.KEY: {},
            nmstate.Route.KEY: {},
            nmstate.RouteRule.KEY: {},
        }
        yield state.return_value


@pytest.fixture(autouse=True)
def rconfig_mock():
    with mock.patch.object(api, 'RunningConfig') as rconfig:
        rconfig.return_value.networks = {}
        rconfig.return_value.bonds = {}
        rconfig.return_value.devices = {}
        yield rconfig.return_value
