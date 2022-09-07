# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

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
