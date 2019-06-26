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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#

from __future__ import absolute_import

import pytest

from yajsonrpc import stomp
from yajsonrpc.stompserver import StompDetector


class Dummy(object):
    def __init__(self):
        self.reactor = None


@pytest.mark.parametrize("data", [c.encode("utf-8") for c in stomp.COMMANDS])
def test_stomp_detector_should_detect_stomp_protocol(data):
    assert StompDetector(json_binding=Dummy()).detect(data)


@pytest.mark.parametrize("data", [
    b"smth",
    b"\x23\x54",
])
def test_stomp_detector_should_reject_garbage(data):
    assert not StompDetector(json_binding=Dummy()).detect(data)


@pytest.mark.parametrize("data", [
    b"GET /",
    b"PUT /",
])
def test_stomp_detector_should_reject_http_verbs(data):
    assert not StompDetector(json_binding=Dummy()).detect(data)
