#
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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA
# 02110-1301  USA
#
# Refer to the README and COPYING files for full details of the license
#

import pytest

from vdsm.virt import utils


@pytest.mark.parametrize(
    "pdiv, expected", [
        ({}, False),
        ({"path": "/some/path"}, False),
        ({
            "poolID": "13345997-b94f-42dd-b8ef-a1392f65cebf",
            "domainID": "88252cf6-381e-48f0-8795-a294a32c7149",
            "imageID": "89f05c7d-b961-4935-993f-514499024515",
            "volumeID": "626a493f-5214-4337-b580-96a1ce702c2a",
        }, True),
        ({
            "additional": "item",
            "poolID": "13345997-b94f-42dd-b8ef-a1392f65cebf",
            "domainID": "88252cf6-381e-48f0-8795-a294a32c7149",
            "imageID": "89f05c7d-b961-4935-993f-514499024515",
            "volumeID": "626a493f-5214-4337-b580-96a1ce702c2a",
        }, True),
        ({
            "poolID": "",
            "domainID": "",
            "imageID": "",
            "volumeID": "",
        }, True)]
)
def test_is_vdsm_image(pdiv, expected):
    assert expected == utils.isVdsmImage(pdiv)
