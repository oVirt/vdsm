# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

import pytest

from vdsm.network import driverloader


def test_load_non_existing_driver():
    _drivers = driverloader.load_drivers('ClassName', 'foo.bar', '/no/drivers')
    with pytest.raises(driverloader.NoDriverError):
        return driverloader.get_driver('shrubbery', _drivers)
