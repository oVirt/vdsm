# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division
from collections import namedtuple
import os
from time import time
from unittest import mock

import pytest

from vdsm.network import connectivity
from vdsm.network.errors import ConfigNetworkError


def _mock_os_stat_with_current_time(unused):
    return namedtuple('st', ['st_mtime'])(time())


def _mock_os_stat_with_zeroed_time(unused):
    return namedtuple('st', ['st_mtime'])(0)


class TestConnectivity(object):
    def test_check_disabled(self):
        connectivity.check({'connectivityCheck': False})

    @mock.patch.object(os, 'stat', _mock_os_stat_with_current_time)
    def test_check_default_timeout_success(self):
        connectivity.check({})

    @mock.patch.object(os, 'stat', _mock_os_stat_with_current_time)
    def test_check_timeout_success(self):
        connectivity.check({'connectivityTimeout': 2})

    @mock.patch.object(os, 'stat', _mock_os_stat_with_zeroed_time)
    def test_check_timeout_fail(self):
        with pytest.raises(ConfigNetworkError):
            connectivity.check({'connectivityTimeout': 0})
