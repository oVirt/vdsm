# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

import pytest

from vdsm.common import nbdutils

# This code is based on imageio -
# https://github.com/oVirt/ovirt-imageio/blob/
# e2fd416f026eee3b7b4acd4fc7c867ceb7ab87f1/
# common/test/nbd_test.py#L29


@pytest.mark.parametrize("addr,export,url", [
    (nbdutils.UnixAddress("/sock"), None, "nbd:unix:/sock"),
    (nbdutils.UnixAddress("/sock"), "", "nbd:unix:/sock"),
    (nbdutils.UnixAddress("/sock"), "sda", "nbd:unix:/sock:exportname=sda"),
    (nbdutils.TCPAddress("host", 0), None, "nbd:host:0"),
    (nbdutils.TCPAddress("host", 10900), "", "nbd:host:10900"),
    (nbdutils.TCPAddress(
        "host", 65535), "sdb", "nbd:host:65535:exportname=sdb"),
])
def test_url(addr, export, url):
    assert addr.url(export) == url


@pytest.mark.parametrize("port", [-1, 65535 + 1])
def test_invalid_tcp_port(port):
    with pytest.raises(ValueError):
        nbdutils.TCPAddress("host", port)
