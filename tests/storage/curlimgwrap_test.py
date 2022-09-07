# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

from vdsm.storage import curlImgWrap


GLANCE_SERVER_HEADER_OUTPUT = b"""HTTP/1.1 200 OK
Content-Type: text/html; charset=UTF-8
Content-Length: 0
X-Image-Meta-Id: 9bd0dd9d-d96a-4606-8e43-12e59d073ec6
X-Image-Meta-Deleted: False
X-Image-Meta-Container_format: bare
X-Image-Meta-Checksum: 70506585249edb66be8f33c22e674dbd
X-Image-Meta-Protected: False
X-Image-Meta-Min_disk: 0
X-Image-Meta-Created_at: 2019-08-05T13:01:37.000000
X-Image-Meta-Size: 307494912
X-Image-Meta-Status: active
X-Image-Meta-Is_public: True
X-Image-Meta-Min_ram: 0
X-Image-Meta-Owner: 186c4df6a4a44d52955c36c58afc788f
X-Image-Meta-Updated_at: 2019-08-05T13:01:41.000000
X-Image-Meta-Disk_format: qcow2
X-Image-Meta-Name: Fedora 29 Cloud Base Image v1.2 for x86_64
Etag: 70506585249edb66be8f33c22e674dbd
X-Openstack-Request-Id: req-146ffb74-0a8d-4716-bdb2-01e7a265aec3
Date: Wed, 04 Sep 2019 13:03:59 GMT

"""


def test_parse_headers(tmpdir):
    headers = curlImgWrap.parse_headers(GLANCE_SERVER_HEADER_OUTPUT)

    # In the vdsm code we use only Content-Length and X-Image-Meta-Size headers
    # so we test only these headers.
    assert headers[u"Content-Length"] == u"0"
    assert headers[u"X-Image-Meta-Size"] == u"307494912"
