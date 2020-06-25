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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license

import pytest

from network.compat import mock

from vdsm.network import nmstate
from vdsm.network.nmstate import api


@pytest.fixture(autouse=True)
def current_state_mock():
    with mock.patch.object(api, 'state_show') as state:
        state.return_value = {nmstate.Interface.KEY: []}
        yield state.return_value


@pytest.fixture(autouse=True)
def rconfig_mock():
    with mock.patch.object(api, 'RunningConfig') as rconfig:
        rconfig.return_value.networks = {}
        rconfig.return_value.bonds = {}
        rconfig.return_value.devices = {}
        yield rconfig.return_value
