#
# Copyright 2018-2019 Red Hat, Inc.
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

from contextlib import contextmanager

import pytest

from . import netfunctestlib as nftestlib
from .netfunctestlib import Target
from network.compat import mock

from vdsm.network import initializer


def pytest_addoption(parser):
    parser.addoption(
        '--target-service', action='store_const', const=Target.SERVICE
    )
    parser.addoption('--target-lib', action='store_const', const=Target.LIB)
    parser.addoption(
        '--skip-stable-link-monitor', action='store_const', const=True
    )


@pytest.fixture(scope='session', autouse=True)
def target(request):

    target_lib = request.config.getoption('--target-lib')
    target_service = request.config.getoption('--target-service')

    if target_lib is None and target_service is None:
        target_proxy = Target.SERVICE
    elif target_lib == Target.LIB and target_service == Target.SERVICE:
        raise Exception("error")
    elif target_service == Target.SERVICE:
        target_proxy = Target.SERVICE
    elif target_lib == Target.LIB:
        target_proxy = Target.LIB

    return target_proxy


@pytest.fixture(scope='session', autouse=True)
def init_lib():
    initializer.init_privileged_network_components()


@pytest.fixture(scope='session')
def skip_stable_link_monitor(request):
    return request.config.getoption(
        '--skip-stable-link-monitor', default=False
    )


@pytest.fixture(scope='session', autouse=True)
def patch_stable_link_monitor(skip_stable_link_monitor):
    if skip_stable_link_monitor:
        with mock.patch.object(
            nftestlib, 'monitor_stable_link_state', nullcontext
        ):
            yield
            return
    yield


@pytest.fixture(scope='session', autouse=True)
def _bond_option_mapping(bond_option_mapping):
    return


@contextmanager
def nullcontext(*args, **kwargs):
    yield
