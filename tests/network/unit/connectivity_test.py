#
# Copyright 2016-2019 Red Hat, Inc.
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
from collections import namedtuple
import os
from time import time

from network.compat import mock

from vdsm.network import connectivity
from vdsm.network.errors import ConfigNetworkError

from testlib import VdsmTestCase


def _mock_os_stat_with_current_time(unused):
    return namedtuple('st', ['st_mtime'])(time())


def _mock_os_stat_with_zeroed_time(unused):
    return namedtuple('st', ['st_mtime'])(0)


class TestConnectivity(VdsmTestCase):
    def test_check_disabled(self):
        with self.assertNotRaises():
            connectivity.check({'connectivityCheck': False})

    @mock.patch.object(os, 'stat', _mock_os_stat_with_current_time)
    def test_check_default_timeout_success(self):
        with self.assertNotRaises():
            connectivity.check({})

    @mock.patch.object(os, 'stat', _mock_os_stat_with_current_time)
    def test_check_timeout_success(self):
        with self.assertNotRaises():
            connectivity.check({'connectivityTimeout': 2})

    @mock.patch.object(os, 'stat', _mock_os_stat_with_zeroed_time)
    def test_check_timeout_fail(self):
        with self.assertRaises(ConfigNetworkError):
            connectivity.check({'connectivityTimeout': 0})
