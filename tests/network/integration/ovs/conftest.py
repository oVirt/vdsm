#
# Copyright 2018 Red Hat, Inc.
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
from __future__ import division

from contextlib import contextmanager

import pytest

from network.nettestlib import running_on_centos
from network.nettestlib import running_on_travis_ci
from network.ovsnettestlib import cleanup_bridges
from network.ovsnettestlib import OvsService


@pytest.fixture(scope='session', autouse=True)
def ovs_service():
    service = OvsService()
    with xfail_when_running_on_travis_with_centos():
        service.setup()
    try:
        yield
    finally:
        service.teardown()


@pytest.fixture(scope='function', autouse=True)
def ovs_cleanup_bridges():
    try:
        yield
    finally:
        cleanup_bridges()


@contextmanager
def xfail_when_running_on_travis_with_centos():
    try:
        yield
    except AssertionError:
        if running_on_travis_ci() and running_on_centos():
            pytest.xfail('Unable to run OVS tests on travis-ci')
        else:
            raise
