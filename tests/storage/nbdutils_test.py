#
# Copyright 2019 Red Hat, Inc.
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
