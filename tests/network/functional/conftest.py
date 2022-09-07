# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from contextlib import contextmanager
from unittest import mock

import pytest

from . import netfunctestlib as nftestlib
from .netfunctestlib import NetFuncTestAdapter
from .netfunctestlib import Target

from vdsm.network import initializer
from vdsm.network.dhcp_monitor import MonitoredItemPool


def pytest_addoption(parser):
    parser.addoption(
        '--target-service', action='store_const', const=Target.SERVICE
    )
    parser.addoption('--target-lib', action='store_const', const=Target.LIB)
    parser.addoption(
        '--skip-stable-link-monitor', action='store_const', const=True
    )


@pytest.fixture(scope='session', autouse=True)
def adapter(target):
    yield NetFuncTestAdapter(target)


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


@pytest.fixture(scope='function', autouse=True)
def clear_monitor_pool():
    yield
    pool = MonitoredItemPool.instance()
    if not pool.is_pool_empty():
        # Some tests are not able to clear the pool
        # (without running dhcp server).
        # The same applies if the waiting for dhcp monitor times out.
        pool.clear_pool()


@contextmanager
def nullcontext(*args, **kwargs):
    yield
