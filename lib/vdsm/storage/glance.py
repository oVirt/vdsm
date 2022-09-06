# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Support for upload and download images to/from OpenStack Glance service.

Relevant API calls are described bellow. Currently, engine still uses v1 API
so we provide support for v1 API here as well, but this API is deprecated and
v2 API should be used.

v1 API, for more details see
https://docs.openstack.org/glance/latest/user/glanceapi.html
- get image metadata: HEAD /v1/images/{image_id}
- get image content: GET /v1/images/{image_id}
- upload new image: POST /v1/images

v2 API, for more details see https://docs.openstack.org/api-ref/image/v2/
- get image metadata: GET /v2/images/{image_id}
- get image content: GET /v2/images/{image_id}/file
- upload new image: POST /v2/images


Example of v1 API (trimmed) metadata response:

    HTTP/1.1 200 OK
    Content-Type: text/html; charset=UTF-8
    Content-Length: 0
    X-Image-Meta-Id: 34bec3e1-7526-474a-ac80-225209a5ad54
    X-Image-Meta-Name: CirrOS 0.4.0 for x86_64
    X-Image-Meta-Size: 12716032
    [...]
    Etag: 443b7623e27ecf03dc9e01ee93f67afe
    X-Openstack-Request-Id: req-6a0c7a56-f1e4-42cd-a344-d1efa361154a
    Date: Wed, 20 May 2020 13:05:50 GMT


Example of v2 API (trimmed) metadata response:

    {
        "id": "34bec3e1-7526-474a-ac80-225209a5ad54",
        "name": "CirrOS 0.4.0 for x86_64",
        "size": 12716032,
        [...]
        "schema": "/v2/schemas/image"
    }

"""

import json
import logging

from urllib.parse import urlparse

from vdsm import utils

from vdsm.common import errors

from vdsm.storage import curlImgWrap

log = logging.getLogger("storage.glance")


class ApiVersionError(errors.Base):
    msg = "Wrong API version or malformed URL: {self.url}"

    def __init__(self, url):
        self.url = url


class ImageSizeError(errors.Base):
    msg = ("Unable to get image size: {self.reason}, url={self.url},"
           "headers={self.headers}, response={self.response}")

    def __init__(self, reason, url, headers, response):
        self.reason = reason
        self.url = url
        self.headers = headers
        self.response = response


def _image_info_v1(url, headers=None):
    """
    Return dict with image info. Only the "size" key is reported for v1 API.
    """
    resp = curlImgWrap.head(url, headers)

    try:
        return {"size": int(resp['X-Image-Meta-Size'])}
    except (KeyError, ValueError) as e:
        raise ImageSizeError(str(e), url, headers, resp)


def _image_info_v2(url, headers=None):
    """
    Return dict with image info as returned by glance.
    See glance docs for more info:
    https://docs.openstack.org/api-ref/image/v2/#show-image
    """
    out = curlImgWrap.get(url, headers)
    info = json.loads(out)

    if "size" not in info:
        raise ImageSizeError("Size not in response", url, headers, info)

    return info


def api_version(url):
    path = urlparse(url).path.lower()
    return path.split("/")[1]


def image_info(url, headers=None):
    """
    Return image info.
    """
    version = api_version(url)

    if version == "v1":
        return _image_info_v1(url, headers)
    elif version == "v2":
        return _image_info_v2(url, headers)
    else:
        raise ApiVersionError(url)


def download_image(image_path, url, headers=None):
    # In case of v2 API, we need to append /file suffix to URL to download
    # image content. In case of v1 API, no URL changes are needed.
    if api_version(url) == "v2":
        url = url + "/file"

    with utils.stopwatch(
            "Downloading {} to {}".format(url, image_path),
            level=logging.INFO,
            log=log):
        curlImgWrap.download(url, image_path, headers)


def upload_image(image_path, url, headers=None):
    with utils.stopwatch(
            "Uploading {} to {}".format(image_path, url),
            level=logging.INFO,
            log=log):
        curlImgWrap.upload(url, image_path, headers)
