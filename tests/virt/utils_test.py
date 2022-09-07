# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

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
