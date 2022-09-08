#
# Copyright 2016-2017 Red Hat, Inc.
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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#

from __future__ import absolute_import
from __future__ import division

"""
Ensure that lib/vdsm modules do not depend on private modules in
/usr/share/vdsm.
"""

import importlib
import pkgutil
import pytest


def find_modules():
    """
    Yields fully qualified modules names in the vdsm package.
    """
    expected_to_fail = {
        # TODO: imports os_brick which is a soft dependency
        # remove when os_brick can be required.
        "vdsm.storage.nos_brick",
    }

    def error(name):
        raise

    vdsm_pkg = importlib.import_module("vdsm")
    for _, name, _ in pkgutil.walk_packages(vdsm_pkg.__path__,
                                            prefix="vdsm.",
                                            onerror=error):
        if name in expected_to_fail:
            name = pytest.param(name, marks=pytest.mark.xfail)
        yield name


@pytest.mark.parametrize("name", find_modules())
def test_import(name):
    try:
        importlib.import_module(name)
    except ModuleNotFoundError as e:
        pytest.skip(str(e))
