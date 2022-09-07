# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

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
