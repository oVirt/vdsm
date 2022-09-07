# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Tests for glance module.

Some of the tests (e.g. image download test) are run against real production
Glance server. Besides the fact that these tests can be slow, it's not polite
to run automated tests against public production instances. These tests are
marked as "integration" and by default are skipped. Such tests can be run
manually with

    tox -e storage -- tests/storage/glance_test.py -m integration

"""

import hashlib
import json

import pytest

from vdsm import constants

from vdsm.storage import curlImgWrap
from vdsm.storage import glance

OVIRT_GLANCE_URL = "http://glance.ovirt.org:9292"

pytestmark = pytest.mark.skip("Tests depend on external server")


@pytest.fixture(
    params=[
        pytest.param(OVIRT_GLANCE_URL + "/v1/images", id="v1"),
        pytest.param(OVIRT_GLANCE_URL + "/v2/images", id="v2"),
    ],
    scope="module",
)
def glance_image(request):
    """
    Get the metadata of the smallest image in the oVirt Glance server.
    """
    try:
        out = curlImgWrap.get(
            request.param + "?sort_key=size&sort_dir=asc&limit=1")
    except curlImgWrap.CurlError:
        return pytest.skip("Glance server is not reachable")

    images = json.loads(out)
    smallest_image = images["images"][0]
    smallest_image["url"] = request.param + "/" + smallest_image["id"]
    return smallest_image


def test_api_version():
    url = OVIRT_GLANCE_URL + "/{}/images/34bec3e1-7526-474a-ac80-225209a5ad54"
    assert glance.api_version(url.format("v1")) == "v1"
    assert glance.api_version(url.format("v2")) == "v2"


def test_image_info_error():
    with pytest.raises(glance.ApiVersionError):
        glance.image_info(OVIRT_GLANCE_URL + "/fail")
    with pytest.raises(glance.ApiVersionError):
        glance.image_info(OVIRT_GLANCE_URL + "//v1")
    with pytest.raises(glance.ApiVersionError):
        glance.image_info(OVIRT_GLANCE_URL + "//v2")


def test_image_size(glance_image):
    info = glance.image_info(glance_image["url"])
    assert info["size"] == glance_image["size"]


@pytest.mark.integration
def test_image_download(monkeypatch, tmpdir, glance_image):
    monkeypatch.setattr(
        constants, "EXT_CURL_IMG_WRAP", "../lib/vdsm/storage/curl-img-wrap")

    img_dest = str(tmpdir.join(glance_image["id"]))
    glance.download_image(img_dest, glance_image["url"])

    md5_hash = hashlib.md5()
    with open(img_dest, "rb") as f:
        md5_hash.update(f.read())
    checksum = md5_hash.hexdigest()

    assert checksum == glance_image["checksum"]
