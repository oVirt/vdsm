#
# Copyright 2016 Red Hat, Inc.
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

"""
Ensure that lib/vdsm modules do not depend on private modules in
/usr/share/vdsm.
"""

import importlib
import pkgutil
import pytest

from vdsm import compat
from vdsm import osinfo


def find_modules():
    """
    Yields fully qualified modules names in the vdsm package.
    """
    expected_to_fail = {
        "vdsm.rpc.Bridge",
        "vdsm.rpc.http",
    }

    # blivet fails on import, see https://bugzilla.redhat.com/1450607
    info = osinfo.version()
    if info["name"] == osinfo.OSName.FEDORA and info["version"] == "27":
        expected_to_fail.add("vdsm.gluster.storagedev")

    def error(name):
        raise

    vdsm_pkg = importlib.import_module("vdsm")
    for _, name, _ in pkgutil.walk_packages(vdsm_pkg.__path__,
                                            prefix="vdsm.",
                                            onerror=error):
        if name in expected_to_fail:
            name = pytest.mark.xfail(name)
        yield name


@pytest.mark.parametrize("name", find_modules())
def test_import(name):
    try:
        importlib.import_module(name)
    except compat.Unsupported as e:
        pytest.skip(str(e))
